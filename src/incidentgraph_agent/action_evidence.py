from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from typing import Any

from incidentgraph_agent.errors import InvalidInput
from incidentgraph_agent.evidence import EvidenceValidationError, parse_timestamp
from incidentgraph_agent.validation import validate_identifier

SNAPSHOT_SCHEMA_VERSION = 1
MAX_SNAPSHOT_AGE_SECONDS = 10 * 60
_SNAPSHOT_FIELDS = {
    "schema_version",
    "incident_id",
    "incident_started_at",
    "incident_status",
    "incident_summary",
    "incident_service_ids",
    "relevant_service_ids",
    "affected_services",
    "verified_dependency_paths",
    "deployment_evidence",
    "alert_evidence",
    "deployment_evidence_ids",
    "alert_evidence_ids",
    "causality_status",
    "graph_identity",
    "source_data_timestamps",
    "observed_at",
    "evidence_digest",
}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class EvidenceSnapshotError(ValueError):
    """Trusted graph evidence cannot safely support an incident action."""


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def build_snapshot_from_agent_result(
    result: object,
    *,
    incident_id: str,
    graph_identity: str,
    observed_at: str | None = None,
) -> dict[str, Any]:
    incident_id = _identifier(incident_id, "incident ID")
    if type(result) is not dict:
        raise EvidenceSnapshotError("Investigation did not return a trusted result.")
    contract = result.get("task_contract")
    requirements = contract.get("requirements") if type(contract) is dict else None
    trace = result.get("tool_trace")
    observations = result.get("observations")
    if (
        result.get("status") != "complete"
        or result.get("task_completed") is not True
        or result.get("evidence_complete") is not True
        or result.get("answer_source") != "deterministic"
        or result.get("model_answer_grounded") is not False
        or not isinstance(result.get("answer"), str)
        or not result["answer"].strip()
        or result.get("error") is not None
        or type(contract) is not dict
        or contract.get("supported") is not True
        or contract.get("reason") is not None
        or contract.get("fulfilled_count") != 1
        or contract.get("missing") != []
        or type(requirements) is not list
        or len(requirements) != 1
        or type(requirements[0]) is not dict
        or requirements[0].get("task_type") != "incident_investigation"
        or requirements[0].get("target_id") != incident_id
        or requirements[0].get("required_tool") != "investigate_incident"
        or type(trace) is not list
        or len(trace) != 1
        or type(trace[0]) is not dict
        or trace[0].get("tool") != "investigate_incident"
        or trace[0].get("status") != "success"
        or not _valid_investigation_arguments(trace[0].get("arguments"), incident_id)
        or type(observations) is not list
        or len(observations) != 1
        or type(observations[0]) is not dict
        or observations[0].get("tool") != "investigate_incident"
        or not _valid_investigation_arguments(
            observations[0].get("arguments"), incident_id
        )
        or type(observations[0].get("evidence")) is not dict
    ):
        raise EvidenceSnapshotError(
            "The investigation does not prove complete evidence for the exact incident."
        )

    snapshot = snapshot_from_graph_evidence(
        observations[0]["evidence"],
        incident_id=incident_id,
        graph_identity=graph_identity,
        observed_at=observed_at,
    )
    expected_refs = _required_references(snapshot)
    references = observations[0]["evidence"].get("evidence_refs")
    if (
        type(references) is not list
        or any(not isinstance(item, str) for item in references)
        or not expected_refs.issubset(set(references))
    ):
        raise EvidenceSnapshotError(
            "The investigation evidence references are incomplete."
        )
    return snapshot


def snapshot_from_graph_evidence(
    evidence: object,
    *,
    incident_id: str,
    graph_identity: str,
    observed_at: str | None = None,
) -> dict[str, Any]:
    incident_id = _identifier(incident_id, "incident ID")
    graph_identity = _identifier(graph_identity, "graph identity")
    if type(evidence) is not dict or evidence.get("evidence_complete") is not True:
        raise EvidenceSnapshotError("Graph evidence is missing or incomplete.")
    if evidence.get("paths_truncated") is not False:
        raise EvidenceSnapshotError("Graph path evidence is missing or incomplete.")
    incident = evidence.get("incident")
    if type(incident) is not dict or incident.get("id") != incident_id:
        raise EvidenceSnapshotError("Graph evidence belongs to a different incident.")
    try:
        incident_started_at = _timestamp(incident.get("started_at"), "incident start")
        raw_incident_services = incident.get("involved_service_ids")
        if type(raw_incident_services) is not list or not raw_incident_services:
            raise EvidenceSnapshotError(
                "Incident evidence has no validated service targets."
            )
        incident_services = sorted(
            {_identifier(item, "incident service ID") for item in raw_incident_services}
        )
        if len(incident_services) != len(raw_incident_services):
            raise EvidenceSnapshotError(
                "Incident evidence contains duplicate service IDs."
            )

        affected = evidence.get("potentially_impacted_services")
        if type(affected) is not list or not affected:
            raise EvidenceSnapshotError(
                "No verified dependency path supports this action."
            )
        normalized_affected: list[dict[str, Any]] = []
        flattened_paths: set[tuple[str, tuple[str, ...], int]] = set()
        affected_ids: set[str] = set()
        for item in affected:
            if type(item) is not dict:
                raise EvidenceSnapshotError(
                    "Affected-service evidence has an invalid shape."
                )
            service_id = _identifier(item.get("service_id"), "affected service ID")
            if service_id in incident_services or service_id in affected_ids:
                raise EvidenceSnapshotError(
                    "Affected-service identities are inconsistent."
                )
            affected_ids.add(service_id)
            paths = item.get("evidence_paths")
            if type(paths) is not list or not paths:
                raise EvidenceSnapshotError(
                    "Affected service has no verified dependency path."
                )
            normalized_paths: list[dict[str, Any]] = []
            for path in paths:
                if type(path) is not dict:
                    raise EvidenceSnapshotError(
                        "Dependency path evidence has an invalid shape."
                    )
                incident_service = _identifier(
                    path.get("incident_service_id"), "incident path endpoint"
                )
                service_ids = path.get("service_ids")
                if type(service_ids) is not list or not 2 <= len(service_ids) <= 5:
                    raise EvidenceSnapshotError(
                        "Dependency path has an invalid length."
                    )
                normalized_ids = tuple(
                    _identifier(value, "path service ID") for value in service_ids
                )
                depth = path.get("depth")
                if (
                    incident_service not in incident_services
                    or normalized_ids[0] != service_id
                    or normalized_ids[-1] != incident_service
                    or len(set(normalized_ids)) != len(normalized_ids)
                    or type(depth) is not int
                    or depth != len(normalized_ids) - 1
                    or not 1 <= depth <= 4
                ):
                    raise EvidenceSnapshotError(
                        "Dependency path failed identity validation."
                    )
                record = {
                    "incident_service_id": incident_service,
                    "service_ids": list(normalized_ids),
                    "depth": depth,
                }
                normalized_paths.append(record)
                flattened_paths.add((incident_service, normalized_ids, depth))
            normalized_paths.sort(
                key=lambda path: (
                    path["depth"],
                    path["incident_service_id"],
                    tuple(path["service_ids"]),
                )
            )
            minimum_depth = item.get("minimum_depth")
            actual_minimum = min(path["depth"] for path in normalized_paths)
            if type(minimum_depth) is not int or minimum_depth != actual_minimum:
                raise EvidenceSnapshotError(
                    "Affected-service minimum depth is inconsistent."
                )
            normalized_affected.append(
                {
                    "service_id": service_id,
                    "minimum_depth": minimum_depth,
                    "evidence_paths": normalized_paths,
                }
            )
        normalized_affected.sort(
            key=lambda item: (item["minimum_depth"], item["service_id"])
        )

        deployments = _normalize_events(
            evidence.get("related_deployments"),
            service_ids=set(incident_services),
            timestamp_field="deployed_at",
            label="deployment",
        )
        alerts = _normalize_events(
            evidence.get("related_alerts"),
            service_ids=set(incident_services),
            timestamp_field="timestamp",
            label="alert",
        )
        causality = evidence.get("causality")
        if type(causality) is not dict or causality.get("status") != "unproven":
            raise EvidenceSnapshotError(
                "Incident causality must remain explicitly unproven."
            )
        observed = _datetime(observed_at or utc_now_text(), "snapshot observation time")
    except EvidenceValidationError as exc:
        raise EvidenceSnapshotError(
            "Graph evidence contains an invalid timestamp."
        ) from exc

    verified_paths = [
        {
            "incident_service_id": endpoint,
            "service_ids": list(path),
            "depth": depth,
        }
        for endpoint, path, depth in sorted(
            flattened_paths, key=lambda item: (item[2], item[0], item[1])
        )
    ]
    relevant_services = sorted(
        set(incident_services)
        | affected_ids
        | {service_id for _, path, _ in flattened_paths for service_id in path}
    )
    source_timestamps = sorted(
        {incident_started_at}
        | {item["timestamp"] for item in deployments}
        | {item["timestamp"] for item in alerts}
    )
    content = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "incident_id": incident_id,
        "incident_started_at": incident_started_at,
        "incident_status": _required_text(incident.get("status"), "incident status"),
        "incident_summary": _required_text(incident.get("summary"), "incident summary"),
        "incident_service_ids": incident_services,
        "relevant_service_ids": relevant_services,
        "affected_services": normalized_affected,
        "verified_dependency_paths": verified_paths,
        "deployment_evidence": deployments,
        "alert_evidence": alerts,
        "deployment_evidence_ids": sorted({item["id"] for item in deployments}),
        "alert_evidence_ids": sorted({item["id"] for item in alerts}),
        "causality_status": "unproven",
        "graph_identity": graph_identity,
        "source_data_timestamps": source_timestamps,
    }
    observed_text = _canonical_timestamp(observed)
    signed_content = content | {"observed_at": observed_text}
    digest = hashlib.sha256(canonical_json(signed_content).encode("utf-8")).hexdigest()
    snapshot = signed_content | {"evidence_digest": digest}
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot: object) -> dict[str, Any]:
    if type(snapshot) is not dict or set(snapshot) != _SNAPSHOT_FIELDS:
        raise EvidenceSnapshotError(
            "The stored evidence snapshot has an invalid shape."
        )
    if (
        type(snapshot.get("schema_version")) is not int
        or snapshot["schema_version"] != SNAPSHOT_SCHEMA_VERSION
    ):
        raise EvidenceSnapshotError("The evidence snapshot version is unsupported.")
    _identifier(snapshot.get("incident_id"), "incident ID")
    _identifier(snapshot.get("graph_identity"), "graph identity")
    if (
        _timestamp(snapshot.get("observed_at"), "snapshot observation time")
        != snapshot["observed_at"]
    ):
        raise EvidenceSnapshotError("The snapshot timestamp is not canonical UTC.")
    if (
        _timestamp(snapshot.get("incident_started_at"), "incident start")
        != snapshot["incident_started_at"]
    ):
        raise EvidenceSnapshotError("The incident timestamp is not canonical UTC.")
    _required_text(snapshot.get("incident_status"), "incident status")
    _required_text(snapshot.get("incident_summary"), "incident summary")
    if not isinstance(snapshot.get("evidence_digest"), str) or not _DIGEST.fullmatch(
        snapshot["evidence_digest"]
    ):
        raise EvidenceSnapshotError("The evidence digest is invalid.")
    incident_services = _sorted_identifiers(
        snapshot.get("incident_service_ids"), "incident service IDs", nonempty=True
    )
    if len(incident_services) != len(set(incident_services)):
        raise EvidenceSnapshotError("Incident service IDs must be unique.")

    affected = snapshot.get("affected_services")
    if type(affected) is not list or not affected:
        raise EvidenceSnapshotError("Affected-service evidence is missing.")
    affected_ids: set[str] = set()
    flattened: set[tuple[str, tuple[str, ...], int]] = set()
    for item in affected:
        if type(item) is not dict or set(item) != {
            "service_id",
            "minimum_depth",
            "evidence_paths",
        }:
            raise EvidenceSnapshotError(
                "Affected-service evidence has an invalid shape."
            )
        service_id = _identifier(item["service_id"], "affected service ID")
        if service_id in incident_services or service_id in affected_ids:
            raise EvidenceSnapshotError("Affected-service identities are inconsistent.")
        affected_ids.add(service_id)
        paths = item["evidence_paths"]
        if type(paths) is not list or not paths:
            raise EvidenceSnapshotError("Affected service has no verified paths.")
        normalized_paths: list[tuple[int, str, tuple[str, ...]]] = []
        for path in paths:
            if type(path) is not dict or set(path) != {
                "incident_service_id",
                "service_ids",
                "depth",
            }:
                raise EvidenceSnapshotError(
                    "Dependency path evidence has an invalid shape."
                )
            endpoint = _identifier(
                path["incident_service_id"], "incident path endpoint"
            )
            service_ids = _sorted_identifiers(
                path["service_ids"],
                "path service IDs",
                nonempty=True,
                preserve_order=True,
            )
            depth = path["depth"]
            if (
                endpoint not in incident_services
                or len(service_ids) < 2
                or service_ids[0] != service_id
                or service_ids[-1] != endpoint
                or len(set(service_ids)) != len(service_ids)
                or type(depth) is not int
                or depth != len(service_ids) - 1
                or not 1 <= depth <= 4
            ):
                raise EvidenceSnapshotError(
                    "Dependency path failed identity validation."
                )
            flattened.add((endpoint, tuple(service_ids), depth))
            normalized_paths.append((depth, endpoint, tuple(service_ids)))
        if len(normalized_paths) != len(set(normalized_paths)):
            raise EvidenceSnapshotError("Dependency paths must not be duplicated.")
        normalized_paths.sort()
        minimum_depth = item["minimum_depth"]
        if type(minimum_depth) is not int or minimum_depth != normalized_paths[0][0]:
            raise EvidenceSnapshotError(
                "Affected-service minimum depth is inconsistent."
            )
        expected_path_docs = [
            {"incident_service_id": endpoint, "service_ids": list(ids), "depth": depth}
            for depth, endpoint, ids in normalized_paths
        ]
        if paths != expected_path_docs:
            raise EvidenceSnapshotError("Dependency paths are not canonically ordered.")
    expected_affected_ids = sorted(
        affected_ids,
        key=lambda value: (
            next(
                item["minimum_depth"]
                for item in affected
                if item["service_id"] == value
            ),
            value,
        ),
    )
    if [item["service_id"] for item in affected] != expected_affected_ids:
        raise EvidenceSnapshotError("Affected services are not canonically ordered.")

    relevant = _sorted_identifiers(
        snapshot.get("relevant_service_ids"), "relevant service IDs", nonempty=True
    )
    expected_relevant = sorted(
        set(incident_services)
        | affected_ids
        | {service_id for _, path, _ in flattened for service_id in path}
    )
    if relevant != expected_relevant:
        raise EvidenceSnapshotError(
            "Relevant service identities do not match the evidence."
        )

    deployments = _validate_normalized_events(
        snapshot.get("deployment_evidence"), set(incident_services), "deployment"
    )
    alerts = _validate_normalized_events(
        snapshot.get("alert_evidence"), set(incident_services), "alert"
    )
    if snapshot.get("deployment_evidence_ids") != sorted(
        {item["id"] for item in deployments}
    ):
        raise EvidenceSnapshotError("Deployment evidence references are incomplete.")
    if snapshot.get("alert_evidence_ids") != sorted({item["id"] for item in alerts}):
        raise EvidenceSnapshotError("Alert evidence references are incomplete.")
    if snapshot.get("causality_status") != "unproven":
        raise EvidenceSnapshotError(
            "Incident causality must remain explicitly unproven."
        )
    expected_path_docs = [
        {"incident_service_id": endpoint, "service_ids": list(ids), "depth": depth}
        for endpoint, ids, depth in sorted(
            flattened, key=lambda item: (item[2], item[0], item[1])
        )
    ]
    if snapshot.get("verified_dependency_paths") != expected_path_docs:
        raise EvidenceSnapshotError(
            "Verified path records do not match affected services."
        )
    timestamps = snapshot.get("source_data_timestamps")
    expected_timestamps = sorted(
        {snapshot["incident_started_at"]}
        | {item["timestamp"] for item in deployments}
        | {item["timestamp"] for item in alerts}
    )
    if timestamps != expected_timestamps:
        raise EvidenceSnapshotError(
            "Source timestamps do not match the evidence records."
        )
    content = {
        key: value for key, value in snapshot.items() if key != "evidence_digest"
    }
    actual = hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual, snapshot["evidence_digest"]):
        raise EvidenceSnapshotError(
            "The evidence snapshot digest does not match its contents."
        )
    return snapshot


def ensure_snapshot_fresh(
    snapshot: dict[str, Any], *, now: datetime | None = None
) -> float:
    validate_snapshot(snapshot)
    observed = _datetime(snapshot["observed_at"], "snapshot observation time")
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise EvidenceSnapshotError("The current clock must be timezone-aware.")
    age = (current.astimezone(UTC) - observed).total_seconds()
    if age < 0 or age > MAX_SNAPSHOT_AGE_SECONDS:
        raise EvidenceSnapshotError(
            "The evidence snapshot is stale or from the future."
        )
    return age


def snapshot_matches_current(original: dict[str, Any], current: dict[str, Any]) -> bool:
    validate_snapshot(original)
    validate_snapshot(current)
    return (
        original["incident_id"] == current["incident_id"]
        and original["graph_identity"] == current["graph_identity"]
        and _stable_evidence_digest(original) == _stable_evidence_digest(current)
    )


def source_data_ages(
    snapshot: dict[str, Any], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    validate_snapshot(snapshot)
    current = now or datetime.now(UTC)
    current = current.astimezone(UTC)
    return [
        {
            "timestamp": value,
            "age_seconds": (
                current - _datetime(value, "source timestamp")
            ).total_seconds(),
        }
        for value in snapshot["source_data_timestamps"]
    ]


def _required_references(snapshot: dict[str, Any]) -> set[str]:
    refs = {f"incident:{snapshot['incident_id']}"}
    refs.update(f"service:{item}" for item in snapshot["relevant_service_ids"])
    refs.update(f"deployment:{item}" for item in snapshot["deployment_evidence_ids"])
    refs.update(f"alert:{item}" for item in snapshot["alert_evidence_ids"])
    refs.update(
        "path:" + ">".join(path["service_ids"])
        for path in snapshot["verified_dependency_paths"]
    )
    return refs


def _stable_evidence_digest(snapshot: dict[str, Any]) -> str:
    content = {
        key: value
        for key, value in snapshot.items()
        if key not in {"observed_at", "evidence_digest"}
    }
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


def _sorted_identifiers(
    values: object,
    label: str,
    *,
    nonempty: bool = False,
    preserve_order: bool = False,
) -> list[str]:
    if type(values) is not list or (nonempty and not values):
        raise EvidenceSnapshotError(f"{label.title()} are missing or malformed.")
    identifiers = [_identifier(value, label) for value in values]
    if not preserve_order and identifiers != sorted(identifiers):
        raise EvidenceSnapshotError(f"{label.title()} are not canonically ordered.")
    return identifiers


def _validate_normalized_events(
    records: object, service_ids: set[str], label: str
) -> list[dict[str, str]]:
    if type(records) is not list:
        raise EvidenceSnapshotError(f"{label.title()} evidence is malformed.")
    normalized: list[dict[str, str]] = []
    for record in records:
        if type(record) is not dict or set(record) != {"id", "service_id", "timestamp"}:
            raise EvidenceSnapshotError(
                f"{label.title()} evidence has an invalid shape."
            )
        event_id = _identifier(record["id"], f"{label} evidence ID")
        service_id = _identifier(record["service_id"], f"{label} service ID")
        timestamp = _timestamp(record["timestamp"], f"{label} timestamp")
        if service_id not in service_ids or timestamp != record["timestamp"]:
            raise EvidenceSnapshotError(
                f"{label.title()} evidence failed identity validation."
            )
        normalized.append(
            {"id": event_id, "service_id": service_id, "timestamp": timestamp}
        )
    if normalized != sorted(
        normalized, key=lambda item: (item["id"], item["service_id"])
    ):
        raise EvidenceSnapshotError(
            f"{label.title()} evidence is not canonically ordered."
        )
    return normalized


def _valid_investigation_arguments(arguments: object, incident_id: str) -> bool:
    if type(arguments) is not dict or arguments.get("incident_id") != incident_id:
        return False
    if set(arguments) not in ({"incident_id"}, {"incident_id", "max_depth"}):
        return False
    if "max_depth" in arguments:
        depth = arguments["max_depth"]
        if type(depth) is not int or not 1 <= depth <= 4:
            return False
    return True


def _normalize_events(
    records: object, *, service_ids: set[str], timestamp_field: str, label: str
) -> list[dict[str, str]]:
    if type(records) is not list:
        raise EvidenceSnapshotError(
            f"{label.title()} evidence is missing or malformed."
        )
    normalized: dict[tuple[str, str], dict[str, str]] = {}
    for record in records:
        if type(record) is not dict:
            raise EvidenceSnapshotError(f"{label.title()} evidence is malformed.")
        event_id = _identifier(record.get("id"), f"{label} evidence ID")
        service_id = _identifier(record.get("service_id"), f"{label} service ID")
        if service_id not in service_ids:
            raise EvidenceSnapshotError(
                f"{label.title()} evidence targets an unrelated service."
            )
        timestamp = _timestamp(record.get(timestamp_field), f"{label} timestamp")
        normalized[(event_id, service_id)] = {
            "id": event_id,
            "service_id": service_id,
            "timestamp": timestamp,
        }
    return [normalized[key] for key in sorted(normalized)]


def _identifier(value: object, label: str) -> str:
    try:
        validate_identifier(value, field=label)
    except InvalidInput as exc:
        raise EvidenceSnapshotError(f"{label} is invalid.") from exc
    return value


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise EvidenceSnapshotError(f"{label.title()} is missing or invalid.")
    return value


def _timestamp(value: object, label: str) -> str:
    try:
        parsed = parse_timestamp(value, field=label)
    except (EvidenceValidationError, TypeError) as exc:
        raise EvidenceSnapshotError(f"{label} is invalid.") from exc
    return _canonical_timestamp(parsed)


def _canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceSnapshotError("A timestamp must include a timezone.")
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _datetime(value: str, label: str) -> datetime:
    try:
        return parse_timestamp(value, field=label).astimezone(UTC)
    except EvidenceValidationError as exc:
        raise EvidenceSnapshotError(f"{label} is invalid.") from exc
