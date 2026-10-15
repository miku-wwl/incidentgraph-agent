from __future__ import annotations

import time
from typing import Any

from incidentgraph_agent.database import FalkorStore
from incidentgraph_agent.dataset import load_dataset
from incidentgraph_agent.errors import GraphQueryError

_NODE_UPSERTS = {
    "Service": """
        UNWIND $rows AS row
        MERGE (n:Service {id: row.id})
        SET n.name = row.name,
            n.environment = row.environment,
            n.criticality = row.criticality
    """,
    "Deployment": """
        UNWIND $rows AS row
        MERGE (n:Deployment {id: row.id})
        SET n.version = row.version,
            n.deployed_at = row.deployed_at,
            n.status = row.status
    """,
    "Alert": """
        UNWIND $rows AS row
        MERGE (n:Alert {id: row.id})
        SET n.severity = row.severity,
            n.timestamp = row.timestamp,
            n.description = row.description
    """,
    "Incident": """
        UNWIND $rows AS row
        MERGE (n:Incident {id: row.id})
        SET n.started_at = row.started_at,
            n.status = row.status,
            n.summary = row.summary
    """,
    "Team": """
        UNWIND $rows AS row
        MERGE (n:Team {id: row.id})
        SET n.name = row.name
    """,
}

_RELATIONSHIP_UPSERTS = {
    "DEPENDS_ON": """
        UNWIND $rows AS row
        MATCH (source:Service {id: row.source_id})
        MATCH (target:Service {id: row.target_id})
        MERGE (source)-[:DEPENDS_ON]->(target)
    """,
    "DEPLOYED_TO": """
        UNWIND $rows AS row
        MATCH (source:Deployment {id: row.source_id})
        MATCH (target:Service {id: row.target_id})
        MERGE (source)-[:DEPLOYED_TO]->(target)
    """,
    "ALERTS_ON": """
        UNWIND $rows AS row
        MATCH (source:Alert {id: row.source_id})
        MATCH (target:Service {id: row.target_id})
        MERGE (source)-[:ALERTS_ON]->(target)
    """,
    "INVOLVES": """
        UNWIND $rows AS row
        MATCH (source:Incident {id: row.source_id})
        MATCH (target:Service {id: row.target_id})
        MERGE (source)-[:INVOLVES]->(target)
    """,
    "OWNED_BY": """
        UNWIND $rows AS row
        MATCH (source:Service {id: row.source_id})
        MATCH (target:Team {id: row.target_id})
        MERGE (source)-[:OWNED_BY]->(target)
    """,
}

_COUNT_NODES = {
    "Service": "MATCH (n:Service) RETURN count(n) AS total",
    "Deployment": "MATCH (n:Deployment) RETURN count(n) AS total",
    "Alert": "MATCH (n:Alert) RETURN count(n) AS total",
    "Incident": "MATCH (n:Incident) RETURN count(n) AS total",
    "Team": "MATCH (n:Team) RETURN count(n) AS total",
}
_COUNT_RELATIONSHIPS = "MATCH ()-[r]->() RETURN count(r) AS total"
_CONSTRAINT_POLL_SECONDS = 10.0


def _ensure_node_id_index(store: FalkorStore, label: str) -> None:
    """Create the supported exact-match index used by ID lookup and uniqueness."""
    try:
        store.graph.create_node_range_index(label, "id")
    except Exception:
        # A duplicate-index response is expected on reseed. Verify actual state below.
        pass

    deadline = time.monotonic() + _CONSTRAINT_POLL_SECONDS
    while True:
        rows = store.graph.list_indices().result_set
        indices = [
            row
            for row in rows
            if str(row[0]) == label
            and list(row[1]) == ["id"]
            and str(row[6]).upper() == "NODE"
            and "RANGE" in row[2].get("id", [])
        ]
        statuses = {str(row[7]).upper() for row in indices}
        if "OPERATIONAL" in statuses:
            return
        if "FAILED" in statuses:
            raise GraphQueryError(f"FalkorDB could not index {label}.id.")
        if time.monotonic() >= deadline:
            raise GraphQueryError(f"FalkorDB did not finish indexing {label}.id in time.")
        time.sleep(0.1)


def _ensure_node_id_constraints(store: FalkorStore) -> None:
    """Wait until each stable node ID has an operational uniqueness constraint."""
    for label in _NODE_UPSERTS:
        # Creating the first index also materializes a fresh graph key. FalkorDB's
        # db.constraints() procedure cannot run against an empty, not-yet-created key.
        _ensure_node_id_index(store, label)
        rows = store.read("CALL db.constraints()").result_set

        def matching_constraint(row: Any) -> bool:
            properties = row[2]
            if isinstance(properties, str):
                properties = [properties]
            return (
                str(row[0]).upper() == "UNIQUE"
                and str(row[1]) == label
                and list(properties) == ["id"]
                and str(row[3]).upper() == "NODE"
            )

        constraints = [row for row in rows if matching_constraint(row)]
        if not constraints:
            store.graph.create_node_unique_constraint(label, "id")

        deadline = time.monotonic() + _CONSTRAINT_POLL_SECONDS
        while True:
            rows = store.read("CALL db.constraints()").result_set
            constraints = [row for row in rows if matching_constraint(row)]
            statuses = {str(row[4]).upper() for row in constraints}
            if "OPERATIONAL" in statuses:
                break
            if "FAILED" in statuses:
                raise GraphQueryError(
                    f"FalkorDB could not enforce unique IDs for {label} nodes."
                )
            if time.monotonic() >= deadline:
                raise GraphQueryError(
                    f"FalkorDB did not finish the {label} ID constraint in time."
                )
            time.sleep(0.1)


def seed_dataset(store: FalkorStore) -> dict[str, Any]:
    """MERGE deterministic nodes and edges into the configured project graph."""
    dataset = load_dataset()
    _ensure_node_id_constraints(store)
    for label, query in _NODE_UPSERTS.items():
        store.write(query, {"rows": dataset["nodes"][label]})
    for relation, query in _RELATIONSHIP_UPSERTS.items():
        store.write(query, {"rows": dataset["relationships"][relation]})

    nodes_by_label: dict[str, int] = {}
    for label, query in _COUNT_NODES.items():
        rows = store.read(query).result_set
        nodes_by_label[label] = int(rows[0][0]) if rows else 0
    edge_rows = store.read(_COUNT_RELATIONSHIPS).result_set
    relationship_count = int(edge_rows[0][0]) if edge_rows else 0
    return {
        "graph_name": store.settings.graph_name,
        "nodes_by_label": nodes_by_label,
        "node_count": sum(nodes_by_label.values()),
        "relationship_count": relationship_count,
        "dataset": "synthetic_incident.json",
        "idempotent_upsert": True,
    }
