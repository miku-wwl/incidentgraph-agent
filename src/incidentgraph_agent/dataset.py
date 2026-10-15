from __future__ import annotations

import json
import re
from importlib.resources import files
from typing import Any

from incidentgraph_agent.errors import GraphQueryError

_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_NODE_LABELS = {"Service", "Deployment", "Alert", "Incident", "Team"}


def load_dataset() -> dict[str, Any]:
    path = files("incidentgraph_agent").joinpath("synthetic_incident.json")
    dataset = json.loads(path.read_text(encoding="utf-8"))
    validate_dataset(dataset)
    return dataset


def validate_dataset(dataset: dict[str, Any]) -> None:
    nodes = dataset.get("nodes")
    relationships = dataset.get("relationships")
    if (
        not isinstance(nodes, dict)
        or set(nodes) != _NODE_LABELS
        or not isinstance(relationships, dict)
    ):
        raise GraphQueryError("The bundled synthetic graph dataset is malformed.")

    known: dict[str, set[str]] = {}
    for label, records in nodes.items():
        if not isinstance(records, list):
            raise GraphQueryError("The bundled synthetic graph dataset is malformed.")
        ids: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                raise GraphQueryError("The bundled synthetic graph dataset is malformed.")
            identifier = record.get("id")
            if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
                raise GraphQueryError("The bundled dataset contains an invalid node ID.")
            if identifier in ids:
                raise GraphQueryError("The bundled dataset contains duplicate node IDs.")
            ids.add(identifier)
        known[label] = ids

    endpoints = {
        "DEPENDS_ON": ("Service", "Service"),
        "DEPLOYED_TO": ("Deployment", "Service"),
        "ALERTS_ON": ("Alert", "Service"),
        "INVOLVES": ("Incident", "Service"),
        "OWNED_BY": ("Service", "Team"),
    }
    if set(relationships) != set(endpoints):
        raise GraphQueryError("The bundled dataset has an unsupported relationship type.")
    for relation, records in relationships.items():
        source_label, target_label = endpoints[relation]
        if not isinstance(records, list):
            raise GraphQueryError("The bundled synthetic graph dataset is malformed.")
        for edge in records:
            if (
                not isinstance(edge, dict)
                or not isinstance(edge.get("source_id"), str)
                or not isinstance(edge.get("target_id"), str)
                or edge["source_id"] not in known[source_label]
                or edge["target_id"] not in known[target_label]
            ):
                raise GraphQueryError(
                    "The bundled dataset contains a relationship with a missing endpoint."
                )
