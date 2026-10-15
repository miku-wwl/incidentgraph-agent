from __future__ import annotations

from copy import deepcopy

import pytest

from incidentgraph_agent.errors import IncompleteGraphEvidence, InvalidInput, NotFound
from incidentgraph_agent.graph_tools import GraphToolRegistry


class _Repository:
    def __init__(self):
        self.calls = []
        self.result = {
            "service_id": "checkout-api",
            "paths": [
                {
                    "service_ids": ["checkout-api", "payment-service"],
                    "depth": 1,
                }
            ],
            "evidence_complete": True,
        }
        self.error = None

    def dependencies(self, service_id, *, max_depth):
        self.calls.append(("dependencies", service_id, max_depth))
        if self.error:
            raise self.error
        return deepcopy(self.result)

    def blast_radius(self, service_id, *, max_depth):
        self.calls.append(("blast_radius", service_id, max_depth))
        if self.error:
            raise self.error
        return {
            "failed_service_id": service_id,
            "affected_services": [],
            "evidence_complete": True,
        }

    def correlate(self, *, incident_id=None):
        self.calls.append(("correlate", incident_id))
        if self.error:
            raise self.error
        return {
            "target": {"type": "incident", "id": incident_id},
            "evidence": {"deployments": [], "alerts": []},
            "evidence_complete": True,
        }

    def investigate(self, incident_id, *, max_depth):
        self.calls.append(("investigate", incident_id, max_depth))
        if self.error:
            raise self.error
        return {
            "incident": {
                "type": "incident",
                "id": incident_id,
                "involved_service_ids": ["payment-service"],
            },
            "related_alerts": [
                {"id": "alert-payment-001", "service_id": "payment-service"}
            ],
            "related_deployments": [],
            "potentially_impacted_services": [
                {
                    "service_id": "notification-service",
                    "evidence_paths": [
                        {
                            "service_ids": ["notification-service", "checkout-api"],
                            "depth": 1,
                        }
                    ],
                }
            ],
            "causality": {"status": "unproven"},
            "evidence_complete": True,
        }


def test_tool_catalog_is_fixed_and_schemas_forbid_additional_properties() -> None:
    registry = GraphToolRegistry(_Repository())
    tools = registry.list_tools()

    assert {tool["name"] for tool in tools} == {
        "get_service_dependencies",
        "calculate_blast_radius",
        "correlate_incident_evidence",
        "investigate_incident",
    }
    assert all(tool["input_schema"]["additionalProperties"] is False for tool in tools)


def test_approved_tool_maps_to_existing_repository_and_adds_stable_path_reference() -> (
    None
):
    repository = _Repository()
    registry = GraphToolRegistry(repository)

    result = registry.invoke(
        "get_service_dependencies", {"service_id": "checkout-api", "max_depth": 2}
    )

    assert repository.calls == [("dependencies", "checkout-api", 2)]
    assert result["paths"][0]["service_ids"] == ["checkout-api", "payment-service"]
    assert result["paths"][0]["evidence_id"] == "path:checkout-api>payment-service"
    assert result["evidence_refs"] == [
        "path:checkout-api>payment-service",
        "service:checkout-api",
        "service:payment-service",
    ]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("not_a_tool", {}),
        ("get_service_dependencies", {"service_id": "checkout-api", "cypher": "MATCH"}),
        ("get_service_dependencies", {}),
        ("get_service_dependencies", {"service_id": "Invalid ID"}),
        ("get_service_dependencies", {"service_id": "checkout-api", "max_depth": True}),
        ("get_service_dependencies", {"service_id": "checkout-api", "max_depth": 5}),
        (
            "investigate_incident",
            {"incident_id": "incident-payment-001", "max_depth": 0},
        ),
    ],
)
def test_invalid_or_unapproved_tool_calls_are_rejected(name, arguments) -> None:
    repository = _Repository()
    with pytest.raises(InvalidInput):
        GraphToolRegistry(repository).invoke(name, arguments)
    assert repository.calls == []


def test_graph_errors_propagate_without_conversion_to_success() -> None:
    repository = _Repository()
    repository.error = NotFound("Unknown service id: missing-service.")

    with pytest.raises(NotFound):
        GraphToolRegistry(repository).invoke(
            "get_service_dependencies", {"service_id": "missing-service"}
        )


def test_tool_rejects_incomplete_repository_result() -> None:
    repository = _Repository()
    repository.result["evidence_complete"] = False

    with pytest.raises(IncompleteGraphEvidence):
        GraphToolRegistry(repository).invoke(
            "get_service_dependencies", {"service_id": "checkout-api"}
        )
