from __future__ import annotations

from datetime import datetime
from typing import Any

from incidentgraph_agent.errors import InvalidInput
from incidentgraph_agent.validation import validate_identifier


class EvidenceValidationError(ValueError):
    """A required graph evidence field is absent or has an invalid value."""


_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "Service": ("id", "name", "environment", "criticality"),
    "Deployment": ("id", "version", "deployed_at", "status"),
    "Alert": ("id", "severity", "timestamp", "description"),
    "Incident": ("id", "started_at", "status", "summary"),
    "Team": ("id", "name"),
}
_TIMESTAMP_FIELDS = {"deployed_at", "timestamp", "started_at"}


def parse_timestamp(value: Any, *, field: str) -> datetime:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) < 20
        or value[10] not in "Tt "
    ):
        raise EvidenceValidationError(
            f"{field} must be a non-empty ISO 8601 timestamp."
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        offset = parsed.utcoffset()
    except (OverflowError, TypeError, ValueError) as exc:
        raise EvidenceValidationError(
            f"{field} must be a non-empty ISO 8601 timestamp."
        ) from exc
    if parsed.tzinfo is None or offset is None:
        raise EvidenceValidationError(f"{field} must include a timezone.")
    return parsed


def validate_record(label: str, record: Any) -> dict[str, str]:
    required = _REQUIRED_FIELDS.get(label)
    if required is None or not isinstance(record, dict):
        raise EvidenceValidationError("Graph evidence record has an invalid shape.")

    validated: dict[str, str] = {}
    for field in required:
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise EvidenceValidationError(
                f"{label}.{field} must be a non-empty string."
            )
        if field == "id":
            try:
                validate_identifier(value, field=f"{label}.id")
            except InvalidInput as exc:
                raise EvidenceValidationError(
                    f"{label}.id is not a valid identifier."
                ) from exc
        if field in _TIMESTAMP_FIELDS:
            parse_timestamp(value, field=f"{label}.{field}")
        validated[field] = value
    return validated
