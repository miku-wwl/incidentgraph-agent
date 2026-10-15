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
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
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
