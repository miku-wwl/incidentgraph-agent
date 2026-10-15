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
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
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


if __name__ == "__main__":
    sys.exit(main())
