from __future__ import annotations

from copy import deepcopy
from typing import Any

from incidentgraph_agent.errors import InvalidInput
from incidentgraph_agent.repository import IncidentRepository
from incidentgraph_agent.validation import validate_identifier, validate_max_depth

_MAX_DEPTH = 4

_TOOL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "name": "get_service_dependencies",
        "description": "Read bounded dependency paths from a service.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string"},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 4},
            },
            "required": ["service_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "calculate_blast_radius",
        "description": "Read services that depend on a failed service.",
        "input_schema": {
            "type": "object",
            "properties": {
                "failed_service_id": {"type": "string"},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 4},
            },
            "required": ["failed_service_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "correlate_incident_evidence",
        "description": "Read an incident and its linked deployment and alert evidence.",
        "input_schema": {
            "type": "object",
            "properties": {"incident_id": {"type": "string"}},
            "required": ["incident_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "investigate_incident",
        "description": "Read complete incident, dependency, deployment, and alert evidence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 4},
            },
            "required": ["incident_id"],
            "additionalProperties": False,
        },
    },
)


class GraphToolRegistry:
    """Explicit allowlist mapping model-visible tools to repository reads."""

    def __init__(self, repository: IncidentRepository) -> None:
        self._repository = repository

    def list_tools(self) -> list[dict[str, Any]]:
        return deepcopy(list(_TOOL_CATALOG))

    def invoke(self, name: object, arguments: object) -> dict[str, Any]:
        if not isinstance(name, str) or name not in {
            "get_service_dependencies",
            "calculate_blast_radius",
            "correlate_incident_evidence",
            "investigate_incident",
        }:
            raise InvalidInput("The requested graph tool is not allowlisted.")
        if type(arguments) is not dict:
            raise InvalidInput("Tool arguments must be a JSON object.")

        if name == "get_service_dependencies":
            values = _validated_arguments(
                arguments,
                required={"service_id"},
                optional={"max_depth"},
            )
            result = self._repository.dependencies(
                validate_identifier(values["service_id"], field="service_id"),
                max_depth=_depth(values),
            )
        elif name == "calculate_blast_radius":
            values = _validated_arguments(
                arguments,
                required={"failed_service_id"},
                optional={"max_depth"},
            )
            result = self._repository.blast_radius(
                validate_identifier(
                    values["failed_service_id"], field="failed_service_id"
                ),
                max_depth=_depth(values),
            )
        elif name == "correlate_incident_evidence":
            values = _validated_arguments(
                arguments,
                required={"incident_id"},
                optional=set(),
            )
            result = self._repository.correlate(
                incident_id=validate_identifier(
                    values["incident_id"], field="incident_id"
                )
            )
        else:
            values = _validated_arguments(
                arguments,
                required={"incident_id"},
                optional={"max_depth"},
            )
            result = self._repository.investigate(
                validate_identifier(values["incident_id"], field="incident_id"),
                max_depth=_depth(values),
            )

        if not isinstance(result, dict) or result.get("evidence_complete") is not True:
            from incidentgraph_agent.errors import IncompleteGraphEvidence

            raise IncompleteGraphEvidence(
                "The graph tool did not return complete validated evidence."
            )
        return _attach_evidence_refs(name, result)


def _validated_arguments(
    arguments: dict[str, Any], *, required: set[str], optional: set[str]
) -> dict[str, Any]:
    keys = set(arguments)
    if keys - required - optional:
        raise InvalidInput("Tool arguments contain unsupported fields.")
    if required - keys:
        raise InvalidInput("Tool arguments are missing required fields.")
    return arguments


def _depth(arguments: dict[str, Any]) -> int:
    return validate_max_depth(arguments.get("max_depth", _MAX_DEPTH))


def _attach_evidence_refs(name: str, result: dict[str, Any]) -> dict[str, Any]:
    output = deepcopy(result)
    refs: set[str] = set()

    def add_service(service_id: object) -> None:
        if isinstance(service_id, str) and service_id:
            refs.add(f"service:{service_id}")

    def add_path(path: dict[str, Any]) -> None:
        service_ids = path.get("service_ids")
        if (
            isinstance(service_ids, list)
            and len(service_ids) >= 2
            and all(isinstance(value, str) and value for value in service_ids)
        ):
            for service_id in service_ids:
                add_service(service_id)
            reference = f"path:{'>'.join(service_ids)}"
            path["evidence_id"] = reference
            refs.add(reference)

    def add_event(event: dict[str, Any], kind: str) -> None:
        identifier = event.get("id")
        if isinstance(identifier, str) and identifier:
            reference = f"{kind}:{identifier}"
            event["evidence_id"] = reference
            refs.add(reference)

    if name == "get_service_dependencies":
        add_service(output.get("service_id"))
        for path in output.get("paths", []):
            if isinstance(path, dict):
                add_path(path)
    elif name == "calculate_blast_radius":
        add_service(output.get("failed_service_id"))
        for affected in output.get("affected_services", []):
            if not isinstance(affected, dict):
                continue
            add_service(affected.get("service_id"))
            for path in affected.get("paths", []):
                if isinstance(path, dict):
                    add_path(path)
    elif name == "correlate_incident_evidence":
        target = output.get("target", {})
        if isinstance(target, dict):
            kind = target.get("type")
            identifier = target.get("id")
            if kind in {"incident", "service"} and isinstance(identifier, str):
                refs.add(f"{kind}:{identifier}")
            for service_id in target.get("involved_service_ids", []):
                add_service(service_id)
        evidence = output.get("evidence", {})
        if isinstance(evidence, dict):
            for event in evidence.get("deployments", []):
                if isinstance(event, dict):
                    add_event(event, "deployment")
                    add_service(event.get("service_id"))
            for event in evidence.get("alerts", []):
                if isinstance(event, dict):
                    add_event(event, "alert")
                    add_service(event.get("service_id"))
    else:
        incident = output.get("incident", {})
        if isinstance(incident, dict) and isinstance(incident.get("id"), str):
            refs.add(f"incident:{incident['id']}")
            for service_id in incident.get("involved_service_ids", []):
                add_service(service_id)
        for event, kind in (
            (output.get("related_deployments", []), "deployment"),
            (output.get("related_alerts", []), "alert"),
        ):
            for record in event:
                if isinstance(record, dict):
                    add_event(record, kind)
                    add_service(record.get("service_id"))
        for affected in output.get("potentially_impacted_services", []):
            if not isinstance(affected, dict):
                continue
            add_service(affected.get("service_id"))
            for path in affected.get("evidence_paths", []):
                if isinstance(path, dict):
                    add_path(path)

    output["evidence_refs"] = sorted(refs)
    return output
