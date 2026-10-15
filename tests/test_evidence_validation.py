from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from incidentgraph_agent.dataset import load_dataset, validate_dataset
from incidentgraph_agent.errors import GraphQueryError, IncompleteGraphEvidence
from incidentgraph_agent.repository import IncidentRepository


def _dataset_with(label: str, field: str, value: object, *, remove: bool = False):
    dataset = deepcopy(load_dataset())
    record = dataset["nodes"][label][0]
    if remove:
        record.pop(field)
    else:
        record[field] = value
    return dataset


@pytest.mark.parametrize(
    ("label", "field"),
    [
        ("Service", "name"),
        ("Deployment", "version"),
        ("Alert", "severity"),
        ("Incident", "summary"),
        ("Team", "name"),
    ],
)
def test_bundled_records_require_all_schema_fields(label: str, field: str) -> None:
    with pytest.raises(GraphQueryError):
        validate_dataset(_dataset_with(label, field, "unused", remove=True))


@pytest.mark.parametrize(
    ("label", "field", "value"),
    [
        ("Service", "criticality", None),
        ("Deployment", "version", 42),
        ("Alert", "description", "  "),
        ("Incident", "started_at", "2026-10-15T08:20:00"),
        ("Team", "id", "Invalid Team ID"),
        ("Deployment", "deployed_at", "2026-10-15T08:20:00+99:99"),
        ("Alert", "timestamp", "2026-10-15X08:20:00+13:00"),
    ],
)
def test_bundled_records_reject_invalid_properties(
    label: str, field: str, value: object
) -> None:
    with pytest.raises(GraphQueryError):
        validate_dataset(_dataset_with(label, field, value))


class _RowsStore:
    def __init__(self, *rows):
        self._rows = iter(rows)

    def read(self, query, params=None):
        return SimpleNamespace(result_set=next(self._rows))


def test_graph_service_result_requires_all_service_properties() -> None:
    repository = IncidentRepository(
        _RowsStore([["checkout-api", "Checkout API", None, "critical"]])
    )

    with pytest.raises(IncompleteGraphEvidence):
        repository.dependencies("checkout-api")


def test_graph_incident_result_rejects_missing_status() -> None:
    repository = IncidentRepository(
        _RowsStore([["incident-1", "2026-10-15T08:20:00+13:00", None, "Summary", []]])
    )

    with pytest.raises(IncompleteGraphEvidence):
        repository.correlate(incident_id="incident-1")


@pytest.mark.parametrize(
    "deployment_row",
    [
        ["deploy-1", None, "2026-10-15T08:00:00+13:00", "succeeded", "checkout-api"],
        ["deploy-1", "v1", "2026-10-15T08:00:00", "succeeded", "checkout-api"],
    ],
)
def test_graph_deployment_result_rejects_missing_or_invalid_properties(
    deployment_row: list[object],
) -> None:
    repository = IncidentRepository(
        _RowsStore(
            [["checkout-api", "Checkout API", "production", "critical"]],
            [deployment_row],
            [],
        )
    )

    with pytest.raises(IncompleteGraphEvidence):
        repository.correlate(service_id="checkout-api")


def test_graph_alert_result_rejects_invalid_service_reference() -> None:
    repository = IncidentRepository(
        _RowsStore(
            [["checkout-api", "Checkout API", "production", "critical"]],
            [],
            [["alert-1", "high", "2026-10-15T08:00:00+13:00", "Alert", None]],
        )
    )

    with pytest.raises(IncompleteGraphEvidence):
        repository.correlate(service_id="checkout-api")
