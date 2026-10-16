from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from incidentgraph_agent.config import Settings
from incidentgraph_agent.database import FalkorStore
from incidentgraph_agent.errors import IncidentGraphError
from incidentgraph_agent.repository import IncidentRepository
from incidentgraph_agent.seed import seed_dataset


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="incidentgraph",
        description="Inspect synthetic incident evidence stored in FalkorDB.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="verify local Redis and FalkorDB connectivity")
    commands.add_parser("seed", help="idempotently seed the project-owned graph")

    dependencies = commands.add_parser(
        "dependencies", help="show direct and multi-hop dependencies"
    )
    dependencies.add_argument("service_id")
    dependencies.add_argument("--max-depth", type=int, default=4)

    blast_radius = commands.add_parser(
        "blast-radius", help="find services that depend on a failed service"
    )
    blast_radius.add_argument("failed_service_id")
    blast_radius.add_argument("--max-depth", type=int, default=4)

    correlate = commands.add_parser(
        "correlate", help="return deployment and alert evidence"
    )
    target = correlate.add_mutually_exclusive_group(required=True)
    target.add_argument("--service")
    target.add_argument("--incident")

    investigate = commands.add_parser(
        "investigate", help="summarize evidence for an incident"
    )
    investigate.add_argument("incident_id")
    investigate.add_argument("--max-depth", type=int, default=4)

    agent = commands.add_parser("agent", help="run the local evidence-grounded Agent")
    agent_commands = agent.add_subparsers(dest="agent_command", required=True)
    ask = agent_commands.add_parser(
        "ask", help="investigate a request with local Ollama"
    )
    ask.add_argument("request")

    mcp = commands.add_parser("mcp", help="run the local read-only MCP server")
    mcp_commands = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_commands.add_parser("serve", help="serve trusted graph tools over stdio")

    web = commands.add_parser("web", help="run the local browser console")
    web_commands = web.add_subparsers(dest="web_command", required=True)
    web_serve = web_commands.add_parser(
        "serve", help="serve the local IncidentGraph console"
    )
    web_serve.add_argument(
        "--home", required=True, help="already initialized private Action home"
    )
    web_serve.add_argument("--port", type=int, default=8765)

    action = commands.add_parser(
        "action", help="manage a separately gated local incident ticket action"
    )
    action_commands = action.add_subparsers(dest="action_command", required=True)
    for name, help_text in (
        ("init", "create isolated local action stores and Runtime identity"),
        (
            "propose",
            "investigate with local Ollama and create an awaiting-approval Run",
        ),
        ("review", "show the exact Plan and evidence snapshot for a Run"),
        ("approve", "approve a Run after exact human confirmation"),
        ("reject", "reject a Run after exact human confirmation"),
        ("admit", "admit an approved Run as a pending Action"),
        ("authorize", "independently authorize one pending Action"),
        ("execute", "execute one authorized local ticket action"),
        ("status", "inspect one Run or Action status"),
        ("tickets", "list locally persisted incident tickets"),
    ):
        subcommand = action_commands.add_parser(name, help=help_text)
        subcommand.add_argument("--home", help="consumer-owned action data directory")
    action_commands.choices["propose"].add_argument("incident_id")
    for name in ("review", "approve", "reject", "admit"):
        action_commands.choices[name].add_argument("run_id")
    for name in ("authorize", "execute", "status"):
        action_commands.choices[name].add_argument("action_id")
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "action":
            return _run_action_command(args)
        if args.command == "web":
            from incidentgraph_agent.web_console import WebConsoleError, serve

            try:
                serve(args.home, args.port)
                return 0
            except WebConsoleError as exc:
                print(
                    json.dumps({"error": {"code": exc.code, "message": str(exc)}}),
                    file=sys.stderr,
                )
                return 2
            except OSError:
                print(
                    "The local Web Console could not bind its loopback listener.",
                    file=sys.stderr,
                )
                return 2
        if args.command == "mcp":
            from incidentgraph_agent.mcp_server import serve_stdio

            try:
                serve_stdio()
                return 0
            except IncidentGraphError as exc:
                print(
                    json.dumps({"error": {"code": exc.code, "message": str(exc)}}),
                    file=sys.stderr,
                )
                return 2
            except Exception:
                print(
                    "The local IncidentGraph MCP server failed to start.",
                    file=sys.stderr,
                )
                return 2
        if args.command == "agent":
            from incidentgraph_agent.agent import AgentLoop, OllamaProvider
            from incidentgraph_agent.graph_tools import GraphToolRegistry

            settings = Settings.from_env()
            with OllamaProvider() as provider:
                with FalkorStore(settings) as store:
                    result = AgentLoop(
                        provider,
                        GraphToolRegistry(IncidentRepository(store)),
                    ).run(args.request)
            _emit(result)
            return 0 if result.get("status") == "complete" else 2

        settings = Settings.from_env()
        with FalkorStore(settings) as store:
            if args.command == "check":
                payload = store.verify()
            elif args.command == "seed":
                payload = seed_dataset(store)
            else:
                repository = IncidentRepository(store)
                if args.command == "dependencies":
                    payload = repository.dependencies(
                        args.service_id, max_depth=args.max_depth
                    )
                elif args.command == "blast-radius":
                    payload = repository.blast_radius(
                        args.failed_service_id, max_depth=args.max_depth
                    )
                elif args.command == "correlate":
                    payload = repository.correlate(
                        service_id=args.service, incident_id=args.incident
                    )
                else:
                    payload = repository.investigate(
                        args.incident_id, max_depth=args.max_depth
                    )
        _emit(payload)
        return 0
    except IncidentGraphError as exc:
        _emit({"error": {"code": exc.code, "message": str(exc)}})
        return 2
    except Exception:
        # Do not print connection strings, credentials, or database internals.
        _emit(
            {
                "error": {
                    "code": "unexpected_error",
                    "message": "The requested local graph operation failed.",
                }
            }
        )
        return 2


def _run_action_command(args: argparse.Namespace) -> int:
    from incidentgraph_agent.ticket_actions import (
        ActionWorkflowError,
        action_status,
        admit_run,
        authorize_action,
        create_action_proposal,
        decide_run,
        default_action_home,
        execute_action,
        list_tickets,
        provision_action_workspace,
        review_run,
    )

    home = default_action_home(args.home)
    try:
        if args.action_command == "init":
            payload = provision_action_workspace(home)
        elif args.action_command == "review":
            payload = review_run(home, args.run_id)
        elif args.action_command == "status":
            payload = action_status(home, args.action_id)
        elif args.action_command == "tickets":
            payload = list_tickets(home)
        elif args.action_command == "propose":
            from incidentgraph_agent.agent import AgentLoop, OllamaProvider
            from incidentgraph_agent.graph_tools import GraphToolRegistry

            settings = Settings.from_env()
            request = f"Investigate {args.incident_id} and explain which services may be affected."
            with OllamaProvider() as provider, FalkorStore(settings) as store:
                result = AgentLoop(
                    provider,
                    GraphToolRegistry(IncidentRepository(store)),
                ).run(request)
                from incidentgraph_agent.action_evidence import (
                    build_snapshot_from_agent_result,
                )

                snapshot = build_snapshot_from_agent_result(
                    result,
                    incident_id=args.incident_id,
                    graph_identity=settings.graph_name,
                )
                payload = create_action_proposal(home, snapshot, graph_store=store)
                payload["investigation"] = {
                    "status": result["status"],
                    "answer_source": result["answer_source"],
                    "model": result["model"],
                    "evidence_digest": snapshot["evidence_digest"],
                }
        elif args.action_command in {
            "approve",
            "reject",
            "admit",
            "authorize",
            "execute",
        }:
            settings = Settings.from_env()
            with FalkorStore(settings) as store:

                def confirm(challenge: str) -> str:
                    prefix = {
                        "approve": "APPROVE",
                        "reject": "REJECT",
                        "authorize": "AUTHORIZE",
                        "execute": "EXECUTE",
                    }[args.action_command]
                    try:
                        return input(f"Type exactly:\n{prefix} {challenge}\n")
                    except (EOFError, KeyboardInterrupt) as exc:
                        raise ActionWorkflowError(
                            "operator_confirmation_cancelled",
                            "Confirmation was cancelled before the lifecycle operation was authorized.",
                        ) from exc

                if args.action_command in {"approve", "reject"}:
                    payload = decide_run(
                        home,
                        args.run_id,
                        approve=args.action_command == "approve",
                        confirm=confirm,
                        graph_store=store,
                    )
                elif args.action_command == "admit":
                    payload = admit_run(home, args.run_id, graph_store=store)
                elif args.action_command == "authorize":
                    payload = authorize_action(
                        home,
                        args.action_id,
                        confirm=confirm,
                        graph_store=store,
                    )
                else:
                    payload = execute_action(
                        home,
                        args.action_id,
                        confirm=confirm,
                        graph_store=store,
                    )
        else:
            raise ActionWorkflowError(
                "unknown_action_command", "Unsupported action command."
            )
        _emit(payload)
        return 0
    except (ActionWorkflowError, IncidentGraphError) as exc:
        _emit({"error": {"code": exc.code, "message": str(exc)}})
        return 2
    except KeyboardInterrupt:
        if args.action_command == "execute":
            _emit(
                {
                    "error": {
                        "code": "execution_interrupted",
                        "message": (
                            "Execution was interrupted; the effect may have occurred. "
                            "Inspect the persisted Action status and ticket receipt "
                            "before taking further action. Do not retry execution."
                        ),
                        "action_id": args.action_id,
                        "action_home": str(home),
                        "inspect_action": [
                            "incidentgraph",
                            "action",
                            "status",
                            args.action_id,
                            "--home",
                            str(home),
                        ],
                        "inspect_tickets": [
                            "incidentgraph",
                            "action",
                            "tickets",
                            "--home",
                            str(home),
                        ],
                    }
                }
            )
            return 2
        _emit(
            {
                "error": {
                    "code": "action_operation_interrupted",
                    "message": (
                        "The action lifecycle operation was interrupted. "
                        "Inspect its persisted state before continuing."
                    ),
                }
            }
        )
        return 2
    except EOFError:
        _emit(
            {
                "error": {
                    "code": "action_operation_failed",
                    "message": "The local action operation failed closed.",
                }
            }
        )
        return 2
    except Exception:
        _emit(
            {
                "error": {
                    "code": "action_operation_failed",
                    "message": "The local action operation failed closed.",
                }
            }
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
