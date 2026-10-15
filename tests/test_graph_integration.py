from __future__ import annotations

import json
import socket

from incidentgraph_agent.cli import main
from incidentgraph_agent.errors import InvalidInput, NotFound
from incidentgraph_agent.repository import IncidentRepository
from incidentgraph_agent.seed import seed_dataset


def _counts(store) -> tuple[dict[str, int], int]:
    labels = {
        "Service": "MATCH (n:Service) RETURN count(n)",
        "Deployment": "MATCH (n:Deployment) RETURN count(n)",
        "Alert": "MATCH (n:Alert) RETURN count(n)",
        "Incident": "MATCH (n:Incident) RETURN count(n)",
        "Team": "MATCH (n:Team) RETURN count(n)",
    }
    nodes = {
        label: int(store.read(query).result_set[0][0])
        for label, query in labels.items()
    }
    relationships = int(
        store.read("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    )
    return nodes, relationships


def test_real_falkordb_module_and_graph_query(store):
    evidence = store.verify()
    assert evidence["redis_ping"] == "PONG"
    assert evidence["graph_module"] == "loaded"
    assert evidence["graph_query_command"] == "available"
    assert evidence["graph_name"].startswith("incidentgraph_test_")


def test_seed_contains_expected_nodes_and_relationships(store):
    nodes, relationships = _counts(store)
    assert nodes == {
        "Service": 8,
        "Deployment": 2,
        "Alert": 2,
        "Incident": 1,
        "Team": 4,
    }
    assert relationships == 21


def test_seed_enforces_unique_node_ids_for_every_label(store):
    rows = store.read("CALL db.constraints()").result_set
    actual = {
        (str(row[1]), tuple(row[2]), str(row[3]).upper(), str(row[4]).upper())
        for row in rows
        if str(row[0]).upper() == "UNIQUE"
    }
    expected = {
        (label, ("id",), "NODE", "OPERATIONAL")
        for label in ("Service", "Deployment", "Alert", "Incident", "Team")
    }
    assert expected <= actual


def test_repeated_seed_is_idempotent(store):
    before = _counts(store)
    result = seed_dataset(store)
    after = _counts(store)
    assert after == before
    assert result["node_count"] == 17
    assert result["relationship_count"] == 21


def test_dependency_query_returns_direct_and_multihop_paths(repository):
    result = repository.dependencies("checkout-api")
    expected = {
        ("checkout-api", "order-api"),
        ("checkout-api", "payment-service"),
        ("checkout-api", "order-api", "payment-db"),
        ("checkout-api", "payment-service", "payment-db"),
        ("checkout-api", "payment-service", "ledger-service"),
    }
    actual = {tuple(path["service_ids"]) for path in result["paths"]}
    assert actual == expected
    assert result["direct_dependencies"] == ["order-api", "payment-service"]
    assert result["relationship_direction"] == "A DEPENDS_ON B means A requires B."


def test_blast_radius_traverses_incoming_dependencies(repository):
    result = repository.blast_radius("payment-db")
    assert [item["service_id"] for item in result["affected_services"]] == [
        "order-api",
        "payment-service",
        "checkout-api",
        "notification-service",
    ]
    assert [item["depth"] for item in result["affected_services"]] == [1, 1, 2, 3]
    assert result["total_distinct_affected_services"] == 4


def test_shared_downstream_dependency_preserves_distinct_paths(repository):
    result = repository.blast_radius("payment-db")
    checkout = next(
        item for item in result["affected_services"] if item["service_id"] == "checkout-api"
    )
    paths = {tuple(path["service_ids"]) for path in checkout["paths"]}
    assert paths == {
        ("checkout-api", "order-api", "payment-db"),
        ("checkout-api", "payment-service", "payment-db"),
    }
    assert len(result["affected_services"]) == len(
        {item["service_id"] for item in result["affected_services"]}
    )


def test_unrelated_services_are_not_in_payment_database_blast_radius(repository):
    impacted = {
        item["service_id"]
        for item in repository.blast_radius("payment-db")["affected_services"]
    }
    assert "catalog-api" not in impacted
    assert "search-api" not in impacted
    assert "ledger-service" not in impacted


def test_max_depth_bounds_traversal(repository):
    result = repository.blast_radius("payment-db", max_depth=1)
    assert {item["service_id"] for item in result["affected_services"]} == {
        "order-api",
        "payment-service",
    }
    assert all(item["depth"] == 1 for item in result["affected_services"])
    assert result["total_distinct_affected_services"] == 2


def test_deployment_and_alert_evidence_is_correlated_not_causal(repository):
    result = repository.correlate(incident_id="incident-payment-001")
    assert result["target"]["involved_service_ids"] == [
        "checkout-api",
        "payment-service",
    ]
    assert [item["id"] for item in result["evidence"]["deployments"]] == [
        "deploy-payment-2026-10-15",
        "deploy-checkout-2026-10-12",
    ]
    assert result["evidence"]["deployments"][0]["relative_timing"] == (
        "within_24h_before_incident"
    )
    assert {item["id"] for item in result["evidence"]["alerts"]} == {
        "alert-payment-5xx-001",
        "alert-checkout-timeout-001",
    }
    assert result["causality"]["status"] == "unproven"
    assert result["candidate_explanations"] == [
        {
            "kind": "deployment_and_following_alert_timeline",
            "statement": (
                "Deployment deploy-payment-2026-10-15 to payment-service preceded a "
                "related alert before the incident. This is a lead to investigate, not "
                "proof that the deployment caused the incident."
            ),
            "supporting_evidence_ids": [
                "deploy-payment-2026-10-15",
                "alert-payment-5xx-001",
            ],
            "service_id": "payment-service",
            "causality_status": "unproven",
        }
    ]


def test_investigation_summary_has_explainable_graph_paths(repository):
    result = repository.investigate("incident-payment-001")
    assert result["incident"]["summary"].startswith("Synthetic payment requests")
    assert result["potentially_impacted_count"] == 1
    affected = result["potentially_impacted_services"][0]
    assert affected["service_id"] == "notification-service"
    assert {tuple(item["service_ids"]) for item in affected["evidence_paths"]} == {
        ("notification-service", "checkout-api"),
        ("notification-service", "checkout-api", "payment-service"),
    }
    assert result["causality"]["status"] == "unproven"
    assert len(result["candidate_explanations"]) == 1
    assert result["candidate_explanations"][0]["supporting_evidence_ids"] == [
        "deploy-payment-2026-10-15",
        "alert-payment-5xx-001",
    ]


def test_unknown_service_and_incident_return_not_found(repository):
    try:
        repository.dependencies("missing-service")
    except NotFound as exc:
        assert "Unknown service" in str(exc)
    else:
        raise AssertionError("unknown service should not return an empty result")

    try:
        repository.investigate("missing-incident")
    except NotFound as exc:
        assert "Unknown incident" in str(exc)
    else:
        raise AssertionError("unknown incident should not return an empty result")


def test_invalid_identifiers_and_depth_are_rejected(repository):
    for invalid_id in ("", "checkout-api' RETURN 1 //", "Uppercase"):
        try:
            repository.dependencies(invalid_id)
        except InvalidInput:
            pass
        else:
            raise AssertionError("invalid service IDs must be rejected")

    for invalid_depth in (0, 5, True):
        try:
            repository.dependencies("checkout-api", max_depth=invalid_depth)
        except InvalidInput:
            pass
        else:
            raise AssertionError("invalid traversal depth must be rejected")


def test_cycles_are_bounded_and_repeated_nodes_are_removed(store):
    store.write(
        """
        MERGE (a:Service {id: $left_id})
        SET a.name = 'Cycle A', a.environment = 'test', a.criticality = 'low'
        MERGE (b:Service {id: $right_id})
        SET b.name = 'Cycle B', b.environment = 'test', b.criticality = 'low'
        MERGE (a)-[:DEPENDS_ON]->(b)
        MERGE (b)-[:DEPENDS_ON]->(a)
        """,
        {"left_id": "cycle-a", "right_id": "cycle-b"},
    )
    result = IncidentRepository(store).dependencies("cycle-a", max_depth=4)
    assert result["paths"] == [
        {
            "service_ids": ["cycle-a", "cycle-b"],
            "relationship": "DEPENDS_ON",
            "depth": 1,
        }
    ]
    assert all(item["depth"] <= 4 for item in result["paths"])


def test_graph_ablation_returns_error_without_invented_evidence(
    monkeypatch, capsys
):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unavailable_port = sock.getsockname()[1]

    monkeypatch.setenv("FALKORDB_HOST", "127.0.0.1")
    monkeypatch.setenv("FALKORDB_HOST_PORT", str(unavailable_port))
    monkeypatch.setenv("FALKORDB_PASSWORD", "unused-test-credential")
    monkeypatch.setenv("INCIDENTGRAPH_GRAPH_NAME", "incidentgraph_ablation_test")
    exit_code = main(["investigate", "incident-payment-001"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 2
    assert payload == {
        "error": {
            "code": "database_unavailable",
            "message": "Cannot connect to the configured local FalkorDB instance.",
        }
    }
    assert "potentially_impacted_services" not in payload
