from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from incidentgraph_agent.database import FalkorStore
from incidentgraph_agent.errors import IncompleteGraphEvidence, InvalidInput, NotFound
from incidentgraph_agent.evidence import (
    EvidenceValidationError,
    parse_timestamp,
    validate_record,
)
from incidentgraph_agent.validation import validate_identifier, validate_max_depth

_RESULT_LIMIT = 500
_RESULT_FETCH_LIMIT = _RESULT_LIMIT + 1
_CORRELATION_WINDOW = timedelta(hours=24)


def _ensure_complete_rows(rows: list[Any], *, evidence_kind: str) -> None:
    if len(rows) > _RESULT_LIMIT:
        raise IncompleteGraphEvidence(
            f"{evidence_kind} exceeded the {_RESULT_LIMIT}-row safety limit; "
            "no partial evidence was returned."
        )


def _validated_path(
    value: Any,
    depth_value: Any,
    *,
    max_depth: int,
    start_id: str,
    end_id: str | None = None,
) -> tuple[int, tuple[str, ...]]:
    path = _path_tuple(value)
    if type(depth_value) is not int:
        raise IncompleteGraphEvidence(
            "FalkorDB returned path evidence that could not be validated; "
            "no partial evidence was returned."
        )
    depth = depth_value
    if (
        path is None
        or path[0] != start_id
        or (end_id is not None and path[-1] != end_id)
        or depth != len(path) - 1
        or not 1 <= depth <= max_depth
    ):
        raise IncompleteGraphEvidence(
            "FalkorDB returned path evidence that could not be validated; "
            "no partial evidence was returned."
        )
    return depth, path


_SERVICE_LOOKUP = """
    MATCH (s:Service {id: $service_id})
    RETURN s.id AS id,
           s.name AS name,
           s.environment AS environment,
           s.criticality AS criticality
    LIMIT 1
"""
_DEPENDENCY_PATHS = """
    MATCH p=(source:Service {id: $service_id})-[:DEPENDS_ON*1..4]->(target:Service)
    WHERE length(p) <= $max_depth
      AND all(node IN nodes(p) WHERE single(candidate IN nodes(p) WHERE id(candidate) = id(node)))
    RETURN [n IN nodes(p) | n.id] AS path_ids, length(p) AS depth
    LIMIT $limit
"""
_BLAST_RADIUS_PATHS = """
    MATCH p=(dependent:Service)-[:DEPENDS_ON*1..4]->(failed:Service {id: $service_id})
    WHERE length(p) <= $max_depth AND dependent.id <> failed.id
      AND all(node IN nodes(p) WHERE single(candidate IN nodes(p) WHERE id(candidate) = id(node)))
    RETURN dependent.id AS service_id,
           [n IN nodes(p) | n.id] AS path_ids,
           length(p) AS depth
    LIMIT $limit
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
    try:
        return parse_timestamp(value, field="timestamp")
    except EvidenceValidationError as exc:
        raise IncompleteGraphEvidence(
            "FalkorDB returned evidence with an invalid timestamp."
        ) from exc


def _query_record(label: str, row: Any, fields: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(row, (list, tuple)) or len(row) != len(fields):
        raise IncompleteGraphEvidence(
            "FalkorDB returned evidence that could not be validated; "
            "no partial evidence was returned."
        )
    try:
        return validate_record(label, dict(zip(fields, row, strict=True)))
    except EvidenceValidationError as exc:
        raise IncompleteGraphEvidence(
            "FalkorDB returned evidence with a missing or invalid required field; "
            "no partial evidence was returned."
        ) from exc


def _path_tuple(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    if any(not isinstance(item, str) or not item for item in value):
        return None
    path = tuple(value)
    if len(path) < 2 or len(set(path)) != len(path):
        return None
    try:
        for item in path:
            validate_identifier(item, field="path service ID")
    except InvalidInput:
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
            {
                "service_id": service_id,
                "max_depth": max_depth,
                "limit": _RESULT_FETCH_LIMIT,
            },
        ).result_set
        _ensure_complete_rows(rows, evidence_kind="Dependency paths")

        paths: set[tuple[int, tuple[str, ...]]] = set()
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                raise IncompleteGraphEvidence(
                    "FalkorDB returned path evidence that could not be validated; "
                    "no partial evidence was returned."
                )
            depth, path = _validated_path(
                row[0], row[1], max_depth=max_depth, start_id=service_id
            )
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
                {
                    "service_ids": list(path),
                    "relationship": "DEPENDS_ON",
                    "depth": depth,
                }
                for depth, path in ordered
            ],
            "truncated": False,
            "evidence_complete": True,
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
            "limit": _RESULT_FETCH_LIMIT,
        }
        rows = self.store.read(_BLAST_RADIUS_PATHS, params).result_set
        _ensure_complete_rows(rows, evidence_kind="Blast-radius paths")

        paths_by_service: dict[str, set[tuple[int, tuple[str, ...]]]] = {}
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                raise IncompleteGraphEvidence(
                    "FalkorDB returned path evidence that could not be validated; "
                    "no partial evidence was returned."
                )
            if not isinstance(row[0], str) or not row[0]:
                raise IncompleteGraphEvidence(
                    "FalkorDB returned path evidence that could not be validated; "
                    "no partial evidence was returned."
                )
            service_id = row[0]
            depth, path = _validated_path(
                row[1],
                row[2],
                max_depth=max_depth,
                start_id=service_id,
                end_id=failed_service_id,
            )
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
            "total_distinct_affected_services": len(paths_by_service),
            "paths_truncated": False,
            "evidence_complete": True,
        }

    def correlate(
        self,
        *,
        service_id: str | None = None,
        incident_id: str | None = None,
    ) -> dict[str, Any]:
        if (service_id is None) == (incident_id is None):
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
            if not isinstance(row, (list, tuple)) or len(row) != 5:
                raise IncompleteGraphEvidence(
                    "FalkorDB returned incident evidence that could not be validated."
                )
            incident_record = _query_record(
                "Incident", row[:4], ("id", "started_at", "status", "summary")
            )
            raw_service_ids = row[4]
            if not isinstance(raw_service_ids, (list, tuple)):
                raise IncompleteGraphEvidence(
                    "FalkorDB returned invalid incident service references."
                )
            try:
                service_ids = sorted(
                    {
                        validate_identifier(item, field="involved service ID")
                        for item in raw_service_ids
                    }
                )
            except InvalidInput as exc:
                raise IncompleteGraphEvidence(
                    "FalkorDB returned invalid incident service references."
                ) from exc
            incident_started_at = incident_record["started_at"]
            incident = {
                "id": incident_record["id"],
                "started_at": incident_started_at,
                "status": incident_record["status"],
                "summary": incident_record["summary"],
                "involved_service_ids": service_ids,
            }
            target = {"type": "incident", **incident}
        else:
            assert service_id is not None
            service_id = validate_identifier(service_id, field="service_id")
            service = self._require_service(service_id)
            service_ids = [service_id]
            target = {"type": "service", **service}

        deployments = (
            self.store.read(
                _DEPLOYMENTS_FOR_SERVICES,
                {"service_ids": service_ids, "limit": _RESULT_FETCH_LIMIT},
            ).result_set
            if service_ids
            else []
        )
        alerts = (
            self.store.read(
                _ALERTS_FOR_SERVICES,
                {"service_ids": service_ids, "limit": _RESULT_FETCH_LIMIT},
            ).result_set
            if service_ids
            else []
        )
        _ensure_complete_rows(deployments, evidence_kind="Deployment evidence")
        _ensure_complete_rows(alerts, evidence_kind="Alert evidence")

        deployment_evidence = []
        for row in deployments:
            if not isinstance(row, (list, tuple)) or len(row) != 5:
                raise IncompleteGraphEvidence(
                    "FalkorDB returned deployment evidence that could not be validated."
                )
            record = _query_record(
                "Deployment", row[:4], ("id", "version", "deployed_at", "status")
            )
            try:
                related_service_id = validate_identifier(
                    row[4], field="deployment service ID"
                )
            except InvalidInput as exc:
                raise IncompleteGraphEvidence(
                    "FalkorDB returned deployment evidence with an invalid service ID."
                ) from exc
            deployment_evidence.append(
                {
                    **record,
                    "service_id": related_service_id,
                    "relative_timing": self._relative_timing(
                        record["deployed_at"], incident_started_at
                    ),
                }
            )

        alert_evidence = []
        for row in alerts:
            if not isinstance(row, (list, tuple)) or len(row) != 5:
                raise IncompleteGraphEvidence(
                    "FalkorDB returned alert evidence that could not be validated."
                )
            record = _query_record(
                "Alert", row[:4], ("id", "severity", "timestamp", "description")
            )
            try:
                related_service_id = validate_identifier(
                    row[4], field="alert service ID"
                )
            except InvalidInput as exc:
                raise IncompleteGraphEvidence(
                    "FalkorDB returned alert evidence with an invalid service ID."
                ) from exc
            alert_evidence.append(
                {
                    **record,
                    "service_id": related_service_id,
                    "relative_timing": self._relative_timing(
                        record["timestamp"], incident_started_at
                    ),
                }
            )

        # Stable second sort keeps the tie-break fields ascending, matching Cypher,
        # while ordering the primary timestamp key by actual UTC instant.
        deployment_evidence.sort(key=lambda item: (item["id"], item["service_id"]))
        deployment_evidence.sort(
            key=lambda item: _timestamp(item["deployed_at"]), reverse=True
        )
        alert_evidence.sort(key=lambda item: (item["id"], item["service_id"]))
        alert_evidence.sort(
            key=lambda item: _timestamp(item["timestamp"]), reverse=True
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
                    and deployment_time
                    < _timestamp(alert["timestamp"])
                    <= incident_time
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
            "evidence_truncated": False,
            "evidence_complete": True,
        }

    def investigate(self, incident_id: str, *, max_depth: int = 4) -> dict[str, Any]:
        incident_id = validate_identifier(incident_id, field="incident_id")
        max_depth = validate_max_depth(max_depth)
        correlation = self.correlate(incident_id=incident_id)
        if correlation.get("evidence_complete") is not True or correlation.get(
            "evidence_truncated", True
        ):
            raise IncompleteGraphEvidence(
                "Incident correlation evidence is incomplete; no investigation "
                "result was returned."
            )
        involved = set(correlation["target"]["involved_service_ids"])
        paths_by_service: dict[str, set[tuple[str, tuple[str, ...], int]]] = {}
        paths_truncated = False

        for source_service in sorted(involved):
            radius = self.blast_radius(source_service, max_depth=max_depth)
            if radius.get("evidence_complete") is not True or radius.get(
                "paths_truncated", True
            ):
                raise IncompleteGraphEvidence(
                    "Incident path evidence is incomplete; no investigation result "
                    "was returned."
                )
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
            "evidence_complete": not paths_truncated,
        }

    def _require_service(self, service_id: str) -> dict[str, str]:
        rows = self.store.read(_SERVICE_LOOKUP, {"service_id": service_id}).result_set
        if not rows:
            raise NotFound(f"Unknown service id: {service_id}.")
        record = _query_record(
            "Service",
            rows[0],
            ("id", "name", "environment", "criticality"),
        )
        return {"id": record["id"], "name": record["name"]}

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
