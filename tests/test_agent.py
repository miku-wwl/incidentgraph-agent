from __future__ import annotations

import json

import httpx
import pytest

from incidentgraph_agent.agent import (
    AgentLoop,
    InvalidModelResponse,
    ModelTimeout,
    ModelUnavailable,
    OllamaProvider,
    OllamaSettings,
    _parse_decision,
)
from incidentgraph_agent.errors import (
    DatabaseUnavailable,
)
from incidentgraph_agent.graph_tools import GraphToolRegistry


def _call(tool: str, arguments: dict[str, object]) -> str:
    return json.dumps({"action": "call_tool", "tool": tool, "arguments": arguments})


def _final(answer: str, evidence_ids: list[str]) -> str:
    return json.dumps(
        {"action": "final", "answer": answer, "evidence_ids": evidence_ids}
    )


class _Provider:
    model_name = "fake-local-model"

    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        next_value = self.decisions.pop(0)
        if isinstance(next_value, Exception):
            raise next_value
        return next_value


class _Repository:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.incomplete = False

    def dependencies(self, service_id, *, max_depth):
        self.calls.append(("dependencies", service_id, max_depth))
        if self.failure:
            raise self.failure
        return {
            "service_id": service_id,
            "paths": [
                {
                    "service_ids": ["checkout-api", "payment-service"],
                    "depth": 1,
                }
            ],
            "evidence_complete": not self.incomplete,
        }

    def blast_radius(self, service_id, *, max_depth):
        self.calls.append(("blast_radius", service_id, max_depth))
        if self.failure:
            raise self.failure
        return {
            "failed_service_id": service_id,
            "affected_services": [],
            "evidence_complete": True,
        }

    def correlate(self, *, incident_id=None):
        self.calls.append(("correlate", incident_id))
        if self.failure:
            raise self.failure
        return {
            "target": {
                "type": "incident",
                "id": incident_id,
                "involved_service_ids": ["payment-service"],
            },
            "evidence": {"deployments": [], "alerts": []},
            "evidence_complete": True,
        }

    def investigate(self, incident_id, *, max_depth):
        self.calls.append(("investigate", incident_id, max_depth))
        if self.failure:
            raise self.failure
        return {
            "incident": {
                "type": "incident",
                "id": incident_id,
                "involved_service_ids": ["payment-service"],
            },
            "related_alerts": [
                {"id": "alert-payment-001", "service_id": "payment-service"}
            ],
            "related_deployments": [
                {"id": "deployment-payment-001", "service_id": "payment-service"}
            ],
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
            "evidence_complete": not self.incomplete,
        }


def _agent(provider: _Provider, repository: _Repository | None = None) -> AgentLoop:
    return AgentLoop(provider, GraphToolRegistry(repository or _Repository()))


def test_agent_selects_trusted_tool_then_returns_grounded_evidence() -> None:
    provider = _Provider(
        _call("investigate_incident", {"incident_id": "incident-payment-001"}),
        _final(
            "Incident incident-payment-001 may affect notification-service through notification-service -> checkout-api. Causality remains unproven.",
            [
                "incident:incident-payment-001",
                "path:notification-service>checkout-api",
            ],
        ),
    )

    result = _agent(provider).run(
        "Investigate incident-payment-001 and explain which services may be affected."
    )

    assert result["status"] == "complete"
    assert result["model_answer_grounded"] is True
    assert result["tool_execution_count"] == 1
    assert result["tool_trace"][0]["tool"] == "investigate_incident"
    assert (
        result["observations"][0]["evidence"]["potentially_impacted_services"][0][
            "service_id"
        ]
        == "notification-service"
    )
    assert result["causality"]["status"] == "unproven"


def test_agent_can_take_two_sequential_read_only_steps() -> None:
    provider = _Provider(
        _call("get_service_dependencies", {"service_id": "checkout-api"}),
        _call("correlate_incident_evidence", {"incident_id": "incident-payment-001"}),
        _final(
            "The observed graph includes checkout-api and incident-payment-001; causality remains unproven.",
            ["service:checkout-api", "incident:incident-payment-001"],
        ),
    )
    repository = _Repository()

    result = _agent(provider, repository).run(
        "Inspect checkout-api and the payment incident."
    )

    assert result["status"] == "complete"
    assert result["tool_execution_count"] == 2
    assert [call[0] for call in repository.calls] == ["dependencies", "correlate"]
    assert len(provider.calls) == 3
    assert len(provider.calls[-1]) == 6


@pytest.mark.parametrize(
    ("decision", "expected_code"),
    [
        (
            '{"action":"call_tool","tool":"delete_graph","arguments":{}}',
            "invalid_model_response",
        ),
        (
            '{"action":"call_tool","tool":"get_service_dependencies","arguments":{"service_id":"checkout-api","cypher":"MATCH"}}',
            "invalid_input",
        ),
        ("not json", "invalid_model_response"),
        (
            '{"action":"final","answer":"No evidence needed","evidence_ids":["service:checkout-api"]}',
            "no_grounded_evidence",
        ),
    ],
)
def test_agent_rejects_unknown_tools_extra_arguments_malformed_json_and_unobserved_final(
    decision: str, expected_code: str
) -> None:
    result = _agent(_Provider(decision)).run("Inspect the graph.")
    assert result["status"] == "error"
    assert result["error"]["code"] == expected_code
    assert result["answer"] is None


def test_agent_enforces_four_tool_execution_budget() -> None:
    provider = _Provider(
        *[
            _call("get_service_dependencies", {"service_id": "checkout-api"})
            for _ in range(5)
        ]
    )
    repository = _Repository()

    result = _agent(provider, repository).run("Repeatedly inspect dependencies.")

    assert result["status"] == "error"
    assert result["error"]["code"] == "tool_budget_exhausted"
    assert result["tool_execution_count"] == 4
    assert len(repository.calls) == 4
    assert len(provider.calls) == 5


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (ModelTimeout("timed out"), "model_timeout"),
    ],
)
def test_agent_stops_on_provider_timeout(
    failure: Exception, expected_code: str
) -> None:
    result = _agent(_Provider(failure)).run("Investigate the incident.")
    assert result["status"] == "error"
    assert result["error"]["code"] == expected_code
    assert result["answer"] is None


@pytest.mark.parametrize(
    ("failure", "incomplete", "expected_code", "expected_status"),
    [
        (
            DatabaseUnavailable("Database unavailable."),
            False,
            "database_unavailable",
            "error",
        ),
        (None, True, "incomplete_graph_evidence", "incomplete"),
    ],
)
def test_graph_failures_never_become_successful_agent_answers(
    failure, incomplete, expected_code, expected_status
) -> None:
    repository = _Repository()
    repository.failure = failure
    repository.incomplete = incomplete
    provider = _Provider(
        _call("investigate_incident", {"incident_id": "incident-payment-001"})
    )

    result = _agent(provider, repository).run("Investigate the incident.")

    assert result["status"] == expected_status
    assert result["error"]["code"] == expected_code
    assert result["answer"] is None
    assert len(provider.calls) == 1


def test_unsupported_identifiers_paths_and_causal_claims_use_safe_summary() -> None:
    provider = _Provider(
        _call("investigate_incident", {"incident_id": "incident-payment-001"}),
        _final(
            "ghost-service caused incident-payment-001 over ghost-service -> payment-db.",
            ["incident:incident-payment-001", "path:notification-service>checkout-api"],
        ),
    )

    result = _agent(provider).run("Investigate incident-payment-001.")

    assert result["status"] == "complete"
    assert result["model_answer_grounded"] is False
    assert "ghost-service" not in result["answer"]
    assert "notification-service" in result["answer"]
    assert "notification-service -> checkout-api" in result["answer"]
    assert "Causality remains unproven" in result["answer"]
    assert "incident:incident-payment-001" in result["evidence_ids"]


def test_unrecognized_free_text_claim_uses_structured_evidence_summary() -> None:
    provider = _Provider(
        _call("investigate_incident", {"incident_id": "incident-payment-001"}),
        _final(
            "The notification-service is degraded and users are suffering outages.",
            ["incident:incident-payment-001", "service:notification-service"],
        ),
    )

    result = _agent(provider).run("Investigate incident-payment-001.")

    assert result["status"] == "complete"
    assert result["model_answer_grounded"] is False
    assert "degraded" not in result["answer"]
    assert "notification-service" in result["answer"]


def test_model_json_rejects_duplicate_keys_and_extra_final_fields() -> None:
    with pytest.raises(InvalidModelResponse):
        _parse_decision(
            '{"action":"final","action":"call_tool","answer":"x","evidence_ids":["x"]}'
        )
    with pytest.raises(InvalidModelResponse):
        _parse_decision(
            '{"action":"final","answer":"x","evidence_ids":["x"],"ok":true}'
        )


def test_model_json_accepts_only_an_exact_allowlisted_tool_action() -> None:
    parsed = _parse_decision(
        '{"action":"investigate_incident","arguments":{"incident_id":"incident-payment-001"}}'
    )
    assert parsed == {
        "action": "call_tool",
        "tool": "investigate_incident",
        "arguments": {"incident_id": "incident-payment-001"},
    }
    with pytest.raises(InvalidModelResponse):
        _parse_decision('{"action":"delete_graph","arguments":{}}')


def test_ollama_provider_verifies_local_model_and_uses_bounded_json_request() -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "phi4-mini:latest"}]})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"action":"investigate_incident","arguments":{"incident_id":"incident-payment-001"}}'
                        }
                    }
                ]
            },
        )

    client = httpx.Client(
        base_url="http://127.0.0.1:11434/v1/",
        transport=httpx.MockTransport(handle),
    )
    provider = OllamaProvider(client=client)
    try:
        decision = provider.complete(
            [{"role": "user", "content": "Investigate incident-payment-001."}]
        )
    finally:
        client.close()

    assert len(requests) == 2
    body = json.loads(requests[1].content)
    assert requests[0].url.path.endswith("/models")
    assert requests[1].url.path.endswith("/chat/completions")
    assert body["model"] == "phi4-mini:latest"
    assert body["max_tokens"] == 768
    assert body["response_format"] == {"type": "json_object"}
    assert "incident-payment-001" in decision


def test_ollama_provider_maps_http_timeout_to_model_timeout() -> None:
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout")

    client = httpx.Client(
        base_url="http://127.0.0.1:11434/v1/",
        transport=httpx.MockTransport(timeout),
    )
    provider = OllamaProvider(client=client)
    try:
        with pytest.raises(ModelTimeout):
            provider.complete([{"role": "user", "content": "test"}])
    finally:
        client.close()


def test_ollama_provider_rejects_model_not_installed() -> None:
    client = httpx.Client(
        base_url="http://127.0.0.1:11434/v1/",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"data": [{"id": "other-model"}]})
        ),
    )
    provider = OllamaProvider(client=client)
    try:
        with pytest.raises(ModelUnavailable):
            provider.complete([{"role": "user", "content": "test"}])
    finally:
        client.close()


def test_ollama_configuration_rejects_non_loopback_endpoints_and_unbounded_timeout() -> (
    None
):
    with pytest.raises(Exception):
        OllamaSettings(base_url="https://example.com/v1").validate()
    with pytest.raises(Exception):
        OllamaSettings(timeout_seconds=float("inf")).validate()
