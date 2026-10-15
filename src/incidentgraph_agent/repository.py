from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from incidentgraph_agent.database import FalkorStore
from incidentgraph_agent.errors import NotFound
from incidentgraph_agent.validation import validate_identifier, validate_max_depth

_RESULT_LIMIT = 500
_CORRELATION_WINDOW = timedelta(hours=24)

_SERVICE_LOOKUP = """
    MATCH (s:Service {id: $service_id})
    RETURN s.id AS id, s.name AS name
    LIMIT 1
"""
_DEPENDENCY_PATHS = """
    MATCH p=(source:Service {id: $service_id})-[:DEPENDS_ON*1..4]->(target:Service)
    WHERE length(p) <= $max_depth
    RETURN [n IN nodes(p) | n.id] AS path_ids, length(p) AS depth
    LIMIT $limit
"""
_BLAST_RADIUS_PATHS = """
    MATCH p=(dependent:Service)-[:DEPENDS_ON*1..4]->(failed:Service {id: $service_id})
    WHERE length(p) <= $max_depth AND dependent.id <> failed.id
    RETURN dependent.id AS service_id,
           [n IN nodes(p) | n.id] AS path_ids,
           length(p) AS depth
    LIMIT $limit
"""
_BLAST_RADIUS_COUNT = """
    MATCH p=(dependent:Service)-[:DEPENDS_ON*1..4]->(failed:Service {id: $service_id})
    WHERE length(p) <= $max_depth AND dependent.id <> failed.id
    RETURN count(DISTINCT dependent.id) AS total
"""
_INCIDENT_LOOKUP = """
    MATCH (i:Incident {id: $incident_id})
    OPTIONAL MATCH (i)-[:INVOLVES]->(s:Service)
    RETURN i.id AS id,
           i.started_at AS started_at,
           i.status AS status,
           i.summary AS summary,
           collect(DISTINCT s.id) AS service_ids
    LIMIT 1
"""
_DEPLOYMENTS_FOR_SERVICES = """
    MATCH (d:Deployment)-[:DEPLOYED_TO]->(s:Service)
    WHERE s.id IN $service_ids
    RETURN DISTINCT d.id AS id,
           d.version AS version,
           d.deployed_at AS deployed_at,
           d.status AS status,
           s.id AS service_id
    ORDER BY d.deployed_at DESC, d.id, s.id
    LIMIT $limit
"""
_ALERTS_FOR_SERVICES = """
    MATCH (a:Alert)-[:ALERTS_ON]->(s:Service)
    WHERE s.id IN $service_ids
    RETURN DISTINCT a.id AS id,
           a.severity AS severity,
           a.timestamp AS timestamp,
           a.description AS description,
           s.id AS service_id
    ORDER BY a.timestamp DESC, a.id, s.id
    LIMIT $limit
"""


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _path_tuple(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    path = tuple(str(item) for item in value)
    if len(path) < 2 or len(set(path)) != len(path):
        return None
    return path


class IncidentRepository:
    def __init__(self, store: FalkorStore) -> None:
        self.store = store

    def dependencies(self, service_id: str, *, max_depth: int = 4) -> dict[str, Any]:
        service_id = validate_identifier(service_id, field="service_id")
        max_depth = validate_max_depth(max_depth)
        self._require_service(service_id)
        rows = self.store.read(
            _DEPENDENCY_PATHS,
            {"service_id": service_id, "max_depth": max_depth, "limit": _RESULT_LIMIT},
        ).result_set

        paths: set[tuple[int, tuple[str, ...]]] = set()
        for row in rows:
            path = _path_tuple(row[0])
            depth = int(row[1])
            if path is None or path[0] != service_id or depth != len(path) - 1:
                continue
            if depth <= max_depth:
                paths.add((depth, path))
        ordered = sorted(paths, key=lambda item: (item[0], item[1]))
        return {
            "service_id": service_id,
            "relationship_direction": "A DEPENDS_ON B means A requires B.",
            "max_depth": max_depth,
            "direct_dependencies": sorted(
                {path[1] for depth, path in ordered if depth == 1}
            ),
            "paths": [
                {"service_ids": list(path), "relationship": "DEPENDS_ON", "depth": depth}
                for depth, path in ordered
            ],
            "truncated": len(rows) >= _RESULT_LIMIT,
        }

    def blast_radius(
        self, failed_service_id: str, *, max_depth: int = 4
    ) -> dict[str, Any]:
        failed_service_id = validate_identifier(
            failed_service_id, field="failed_service_id"
        )
        max_depth = validate_max_depth(max_depth)
        self._require_service(failed_service_id)
        params = {
            "service_id": failed_service_id,
            "max_depth": max_depth,
            "limit": _RESULT_LIMIT,
        }
        count_rows = self.store.read(_BLAST_RADIUS_COUNT, params).result_set
        total = int(count_rows[0][0]) if count_rows else 0
        rows = self.store.read(_BLAST_RADIUS_PATHS, params).result_set

        paths_by_service: dict[str, set[tuple[int, tuple[str, ...]]]] = {}
        for row in rows:
            service_id = str(row[0])
            path = _path_tuple(row[1])
            depth = int(row[2])
            if (
                path is None
                or path[0] != service_id
                or path[-1] != failed_service_id
                or depth != len(path) - 1
                or depth > max_depth
            ):
                continue
            paths_by_service.setdefault(service_id, set()).add((depth, path))

        affected: list[dict[str, Any]] = []
        for service_id, paths in paths_by_service.items():
            ordered_paths = sorted(paths, key=lambda item: (item[0], item[1]))
            affected.append(
                {
                    "service_id": service_id,
                    "depth": min(depth for depth, _ in ordered_paths),
                    "paths": [
                        {
                            "service_ids": list(path),
                            "relationship": "DEPENDS_ON",
                            "depth": depth,
                        }
                        for depth, path in ordered_paths
                    ],
                }
            )
        affected.sort(key=lambda item: (item["depth"], item["service_id"]))
        return {
            "failed_service_id": failed_service_id,
            "relationship_direction": (
                "A DEPENDS_ON B means A requires B; paths run from each affected service to the failed service."
            ),
            "max_depth": max_depth,
            "affected_services": affected,
            "total_distinct_affected_services": total,
            "paths_truncated": len(rows) >= _RESULT_LIMIT,
        }

    def correlate(
        self,
        *,
        service_id: str | None = None,
        incident_id: str | None = None,
    ) -> dict[str, Any]:
        if (service_id is None) == (incident_id is None):
            from incidentgraph_agent.errors import InvalidInput

            raise InvalidInput("Supply exactly one of service_id or incident_id.")

        incident: dict[str, Any] | None = None
        incident_started_at: str | None = None
        if incident_id is not None:
            incident_id = validate_identifier(incident_id, field="incident_id")
            rows = self.store.read(
                _INCIDENT_LOOKUP, {"incident_id": incident_id}
            ).result_set
            if not rows:
                raise NotFound(f"Unknown incident id: {incident_id}.")
            row = rows[0]
            service_ids = sorted(
                {str(item) for item in (row[4] or []) if item is not None}
            )
            incident_started_at = str(row[1])
            incident = {
                "id": str(row[0]),
                "started_at": incident_started_at,
                "status": str(row[2]),
                "summary": str(row[3]),
                "involved_service_ids": service_ids,
            }
            target = {"type": "incident", **incident}
        else:
            assert service_id is not None
            service_id = validate_identifier(service_id, field="service_id")
            service = self._require_service(service_id)
            service_ids = [service_id]
            target = {"type": "service", **service}

        deployments = self.store.read(
            _DEPLOYMENTS_FOR_SERVICES,
            {"service_ids": service_ids, "limit": _RESULT_LIMIT},
        ).result_set if service_ids else []
        alerts = self.store.read(
            _ALERTS_FOR_SERVICES,
            {"service_ids": service_ids, "limit": _RESULT_LIMIT},
        ).result_set if service_ids else []

        deployment_evidence = [
            {
                "id": str(row[0]),
                "version": str(row[1]),
                "deployed_at": str(row[2]),
                "status": str(row[3]),
                "service_id": str(row[4]),
                "relative_timing": self._relative_timing(
                    str(row[2]), incident_started_at
                ),
            }
            for row in deployments
        ]
        alert_evidence = [
            {
                "id": str(row[0]),
                "severity": str(row[1]),
                "timestamp": str(row[2]),
                "description": str(row[3]),
                "service_id": str(row[4]),
                "relative_timing": self._relative_timing(
                    str(row[2]), incident_started_at
                ),
            }
            for row in alerts
        ]
        deployment_evidence.sort(
            key=lambda item: (item["deployed_at"], item["id"], item["service_id"]),
            reverse=True,
        )
        alert_evidence.sort(
            key=lambda item: (item["timestamp"], item["id"], item["service_id"]),
            reverse=True,
        )
        candidate_explanations: list[dict[str, Any]] = []
        if incident_started_at is not None:
            incident_time = _timestamp(incident_started_at)
            for deployment in deployment_evidence:
                if deployment["relative_timing"] != "within_24h_before_incident":
                    continue
                deployment_time = _timestamp(deployment["deployed_at"])
                following_alerts = [
                    alert
                    for alert in alert_evidence
                    if alert["service_id"] == deployment["service_id"]
                    and deployment_time < _timestamp(alert["timestamp"]) <= incident_time
                ]
                if not following_alerts:
                    continue
                candidate_explanations.append(
                    {
                        "kind": "deployment_and_following_alert_timeline",
                        "statement": (
                            f"Deployment {deployment['id']} to {deployment['service_id']} "
                            "preceded a related alert before the incident. This is a lead "
                            "to investigate, not proof that the deployment caused the incident."
                        ),
                        "supporting_evidence_ids": [
                            deployment["id"],
                            *sorted(alert["id"] for alert in following_alerts),
                        ],
                        "service_id": deployment["service_id"],
                        "causality_status": "unproven",
                    }
                )
        return {
            "target": target,
            "correlation_window_hours": 24 if incident_started_at else None,
            "evidence": {
                "deployments": deployment_evidence,
                "alerts": alert_evidence,
            },
            "candidate_explanations": candidate_explanations,
            "causality": {
                "status": "unproven",
                "note": "Temporal proximity is correlation, not proof of causation.",
            },
            "uncertainty": [
                "The synthetic graph contains no logs, traces, or metric time series.",
                "Deployment and alert timing alone cannot establish root cause.",
            ],
            "evidence_truncated": (
                len(deployments) >= _RESULT_LIMIT or len(alerts) >= _RESULT_LIMIT
            ),
        }

    def investigate(self, incident_id: str, *, max_depth: int = 4) -> dict[str, Any]:
        incident_id = validate_identifier(incident_id, field="incident_id")
        max_depth = validate_max_depth(max_depth)
        correlation = self.correlate(incident_id=incident_id)
        involved = set(correlation["target"]["involved_service_ids"])
        paths_by_service: dict[str, set[tuple[str, tuple[str, ...], int]]] = {}
        paths_truncated = False

        for source_service in sorted(involved):
            radius = self.blast_radius(source_service, max_depth=max_depth)
            paths_truncated = paths_truncated or radius["paths_truncated"]
            for affected in radius["affected_services"]:
                target_service = affected["service_id"]
                if target_service in involved:
                    continue
                for path in affected["paths"]:
                    paths_by_service.setdefault(target_service, set()).add(
                        (
                            source_service,
                            tuple(path["service_ids"]),
                            int(path["depth"]),
                        )
                    )

        potential_impact: list[dict[str, Any]] = []
        for service_id, records in paths_by_service.items():
            ordered = sorted(records, key=lambda item: (item[2], item[0], item[1]))
            potential_impact.append(
                {
                    "service_id": service_id,
                    "minimum_depth": min(item[2] for item in ordered),
                    "evidence_paths": [
                        {
                            "incident_service_id": source,
                            "service_ids": list(path),
                            "depth": depth,
                        }
                        for source, path, depth in ordered
                    ],
                }
            )
        potential_impact.sort(
            key=lambda item: (item["minimum_depth"], item["service_id"])
        )

        return {
            "incident": correlation["target"],
            "related_alerts": correlation["evidence"]["alerts"],
            "related_deployments": correlation["evidence"]["deployments"],
            "candidate_explanations": correlation["candidate_explanations"],
            "potentially_impacted_services": potential_impact,
            "potentially_impacted_count": len(potential_impact),
            "relationship_direction": (
                "A DEPENDS_ON B means A requires B; blast-radius paths are traversed against the edge direction."
            ),
            "max_depth": max_depth,
            "causality": correlation["causality"],
            "uncertainty": correlation["uncertainty"],
            "paths_truncated": paths_truncated,
        }

    def _require_service(self, service_id: str) -> dict[str, str]:
        rows = self.store.read(_SERVICE_LOOKUP, {"service_id": service_id}).result_set
        if not rows:
            raise NotFound(f"Unknown service id: {service_id}.")
        return {"id": str(rows[0][0]), "name": str(rows[0][1])}

    @staticmethod
    def _relative_timing(value: str, incident_started_at: str | None) -> str:
        if incident_started_at is None:
            return "not_assessed_without_incident_context"
        evidence_time = _timestamp(value)
        incident_time = _timestamp(incident_started_at)
        if evidence_time > incident_time:
            return "after_incident_start"
        if evidence_time >= incident_time - _CORRELATION_WINDOW:
            return "within_24h_before_incident"
        return "older_than_24h_before_incident"
