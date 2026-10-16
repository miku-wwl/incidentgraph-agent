from __future__ import annotations

import json
import threading
import time
from uuid import uuid4

import httpx
import pytest

from incidentgraph_agent import ticket_actions
from incidentgraph_agent.action_store import (
    LocalIncidentTicketStore,
    StoreOutcomeUnknown,
)
from incidentgraph_agent.database import FalkorStore
from incidentgraph_agent.errors import DatabaseUnavailable
from incidentgraph_agent.ticket_actions import (
    ActionWorkspace,
    provision_action_workspace,
)
from incidentgraph_agent.web_console import WebConsoleError, create_server

INCIDENT_ID = "incident-payment-001"


class _InvestigationProvider:
    model_name = "phase4-isolated-test-provider"

    def __init__(self) -> None:
        self.decisions = [
            json.dumps(
                {
                    "action": "investigate_incident",
                    "arguments": {"incident_id": INCIDENT_ID},
                }
            ),
            json.dumps(
                {
                    "action": "final",
                    "answer": "Test-only provider text is not trusted as evidence.",
                    "evidence_ids": [f"incident:{INCIDENT_ID}"],
                }
            ),
        ]

    def complete(self, _messages: list[dict[str, str]]) -> str:
        return self.decisions.pop(0)


@pytest.fixture
def console(tmp_path, store):
    home = tmp_path / "phase4-web-action-home"
    provision_action_workspace(home)
    server = create_server(
        home,
        0,
        store_factory=lambda: FalkorStore(store.settings),
        provider_factory=_InvestigationProvider,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    client = httpx.Client(base_url=base_url, trust_env=False, timeout=5)
    try:
        yield {
            "home": home,
            "server": server,
            "client": client,
            "base_url": base_url,
            "store_settings": store.settings,
        }
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _post(
    client: httpx.Client, path: str, body: dict, *, request_id: str | None = None
):
    data = {**body, "request_id": request_id or str(uuid4())}
    return client.post(
        path,
        json=data,
        headers={
            "Origin": str(client.base_url).rstrip("/"),
            "X-IncidentGraph-Console": "1",
        },
    )


def _wait(client: httpx.Client, job_id: str, timeout: float = 25.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["state"] not in {"queued", "running"}:
            return job
        time.sleep(0.025)
    pytest.fail("The isolated Web Console job did not finish within the test deadline.")


def _job(client: httpx.Client, path: str, body: dict) -> dict:
    response = _post(client, path, body)
    assert response.status_code == 202, response.text
    job = _wait(client, response.json()["job"]["job_id"])
    return job


def _propose(client: httpx.Client) -> tuple[dict, dict]:
    investigation = _job(client, "/api/investigations", {"incident_id": INCIDENT_ID})
    assert investigation["state"] == "complete"
    result = investigation["result"]
    assert result["status"] == "complete"
    proposal = _job(
        client,
        "/api/actions/propose",
        {"investigation_id": result["investigation_id"]},
    )
    assert proposal["state"] == "complete"
    return result, proposal["result"]


def test_graph_catalog_and_local_console_are_read_only_and_secret_free(console):
    client = console["client"]
    health = client.get("/api/health")
    catalog = client.get("/api/catalog")
    page = client.get("/")
    script = client.get("/app.js")
    capability = (
        ActionWorkspace.load(console["home"]).capability_path.read_text("ascii").strip()
    )

    assert health.status_code == 200
    assert health.json()["graph"]["graph_module_version"] == 60002
    assert catalog.status_code == 200
    payload = catalog.json()
    assert len(payload["services"]) == 8
    assert len(payload["incidents"]) == 1
    assert payload["evidence_complete"] is True
    assert {edge["type"] for edge in payload["dependencies"]} == {"DEPENDS_ON"}
    assert all(
        edge["source"] in {item["id"] for item in payload["services"]}
        for edge in payload["dependencies"]
    )
    assert page.status_code == 200
    assert script.status_code == 200
    assert capability not in page.text + script.text + catalog.text + health.text
    assert page.headers["content-security-policy"].find("'self'") >= 0
    assert "Access-Control-Allow-Origin" not in page.headers
    assert client.get("/api/tickets").json()["count"] == 0


def test_write_api_rejects_cross_site_and_invalid_body(console):
    client = console["client"]
    rejected = client.post(
        "/api/investigations",
        json={"request_id": str(uuid4()), "incident_id": INCIDENT_ID},
        headers={"Origin": "https://attacker.invalid", "X-IncidentGraph-Console": "1"},
    )
    missing_header = client.post(
        "/api/investigations",
        json={"request_id": str(uuid4()), "incident_id": INCIDENT_ID},
        headers={"Origin": str(client.base_url).rstrip("/")},
    )
    invalid = _post(
        client, "/api/investigations", {"incident_id": INCIDENT_ID, "extra": True}
    )
    assert rejected.status_code == 403
    assert missing_header.status_code == 403
    assert invalid.status_code == 400
    assert client.get("/api/tickets").json()["count"] == 0


def test_local_action_api_keeps_every_gate_separate_and_duplicate_request_is_idempotent(
    console,
):
    client = console["client"]
    investigation_response = _post(
        client, "/api/investigations", {"incident_id": INCIDENT_ID}
    )
    assert investigation_response.status_code == 202
    investigation_job_id = investigation_response.json()["job"]["job_id"]
    investigation_job = _wait(client, investigation_job_id)
    investigation = investigation_job["result"]
    assert investigation["status"] == "complete"
    assert investigation["task_completed"] is True
    assert investigation["raw_model_prose_used"] is False
    assert investigation["causality"]["status"] == "unproven"

    proposal_request_id = str(uuid4())
    proposal_body = {"investigation_id": investigation["investigation_id"]}
    first = _post(
        client, "/api/actions/propose", proposal_body, request_id=proposal_request_id
    )
    replay = _post(
        client, "/api/actions/propose", proposal_body, request_id=proposal_request_id
    )
    assert first.status_code == 202
    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True
    assert replay.json()["job"]["job_id"] == first.json()["job"]["job_id"]
    proposal_job = _wait(client, first.json()["job"]["job_id"])
    proposal = proposal_job["result"]
    run_id = proposal["run"]["run_id"]
    assert proposal["status"] == "awaiting_approval"
    assert proposal["ticket_count"] == 0

    premature_admission = _job(client, "/api/actions/admit", {"run_id": run_id})
    assert premature_admission["state"] == "failed"
    assert premature_admission["error"]["code"] == "run_not_approved"
    assert client.get("/api/tickets").json()["count"] == 0

    review = client.get(f"/api/review?run_id={run_id}")
    assert review.status_code == 200
    review_data = review.json()
    assert review_data["plan_digest"] == proposal["plan_digest"]
    assert review_data["evidence_digest"] == proposal["evidence_digest"]
    assert review_data["plan"]["risk_class"] == "SIDE_EFFECT"

    wrong_phrase = _job(
        client,
        "/api/actions/decision",
        {"run_id": run_id, "decision": "approve", "confirmation": "APPROVE 0000"},
    )
    assert wrong_phrase["state"] == "failed"
    assert wrong_phrase["error"]["code"] == "operator_confirmation_failed"
    assert client.get("/api/tickets").json()["count"] == 0

    approval = _job(
        client,
        "/api/actions/decision",
        {
            "run_id": run_id,
            "decision": "approve",
            "confirmation": f"APPROVE {review_data['plan_digest']}",
        },
    )
    assert approval["result"]["status"] == "Approved"
    admission = _job(client, "/api/actions/admit", {"run_id": run_id})
    action_id = admission["result"]["action"]["action_id"]
    assert admission["result"]["status"] == "Pending"
    assert client.get("/api/tickets").json()["count"] == 0

    no_authorization = _job(
        client,
        "/api/actions/execute",
        {"action_id": action_id, "confirmation": f"EXECUTE {action_id}"},
    )
    assert no_authorization["state"] == "failed"
    assert client.get("/api/tickets").json()["count"] == 0

    authorization = _job(
        client,
        "/api/actions/authorize",
        {"action_id": action_id, "confirmation": f"AUTHORIZE {action_id}"},
    )
    assert authorization["result"]["status"] == "Authorized"
    execute_request_id = str(uuid4())
    execute_body = {"action_id": action_id, "confirmation": f"EXECUTE {action_id}"}
    execution = _post(
        client, "/api/actions/execute", execute_body, request_id=execute_request_id
    )
    replay_execution = _post(
        client, "/api/actions/execute", execute_body, request_id=execute_request_id
    )
    assert execution.status_code == 202
    assert replay_execution.status_code == 200
    assert replay_execution.json()["job"]["job_id"] == execution.json()["job"]["job_id"]
    executed = _wait(client, execution.json()["job"]["job_id"])
    assert executed["result"]["status"] == "Succeeded"
    assert executed["result"]["receipt_verified"] is True
    assert client.get("/").status_code == 200
    tickets = client.get("/api/tickets").json()
    assert tickets["count"] == 1
    assert tickets["tickets"][0]["action_id"] == action_id
    status = client.get(f"/api/status?action_id={action_id}").json()
    assert status["status"] == "Succeeded"
    assert status["receipt_verified"] is True


def test_rejected_or_changed_evidence_cannot_be_admitted(console, store, monkeypatch):
    client = console["client"]
    _investigation, proposal = _propose(client)
    run_id = proposal["run"]["run_id"]
    review = client.get(f"/api/review?run_id={run_id}").json()
    rejection = _job(
        client,
        "/api/actions/decision",
        {
            "run_id": run_id,
            "decision": "reject",
            "confirmation": f"REJECT {review['plan_digest']}",
        },
    )
    assert rejection["result"]["status"] == "Rejected"
    rejected_admission = _job(client, "/api/actions/admit", {"run_id": run_id})
    assert rejected_admission["state"] == "failed"
    assert client.get("/api/tickets").json()["count"] == 0

    _second_investigation, second_proposal = _propose(client)
    second_run = second_proposal["run"]["run_id"]
    second_review = client.get(f"/api/review?run_id={second_run}").json()
    store.write(
        "MATCH (i:Incident {id: $incident_id}) SET i.summary = $summary",
        {
            "incident_id": INCIDENT_ID,
            "summary": "Synthetic graph evidence changed for regression test.",
        },
    )
    changed = _job(
        client,
        "/api/actions/decision",
        {
            "run_id": second_run,
            "decision": "approve",
            "confirmation": f"APPROVE {second_review['plan_digest']}",
        },
    )
    assert changed["state"] == "failed"
    assert changed["error"]["code"] == "evidence_changed"
    assert client.get("/api/tickets").json()["count"] == 0

    _third_investigation, third_proposal = _propose(client)
    third_run = third_proposal["run"]["run_id"]
    third_review = client.get(f"/api/review?run_id={third_run}").json()
    monkeypatch.setattr(
        ticket_actions,
        "ensure_snapshot_fresh",
        lambda _snapshot: (_ for _ in ()).throw(
            ticket_actions.EvidenceSnapshotError("injected expired snapshot")
        ),
    )
    stale = _job(
        client,
        "/api/actions/decision",
        {
            "run_id": third_run,
            "decision": "approve",
            "confirmation": f"APPROVE {third_review['plan_digest']}",
        },
    )
    assert stale["state"] == "failed"
    assert stale["error"]["code"] == "evidence_unavailable"
    assert client.get("/api/tickets").json()["count"] == 0


def test_unknown_outcome_is_visible_and_cannot_be_retried(console, monkeypatch):
    client = console["client"]
    _investigation, proposal = _propose(client)
    run_id = proposal["run"]["run_id"]
    review = client.get(f"/api/review?run_id={run_id}").json()
    approved = _job(
        client,
        "/api/actions/decision",
        {
            "run_id": run_id,
            "decision": "approve",
            "confirmation": f"APPROVE {review['plan_digest']}",
        },
    )
    assert approved["result"]["status"] == "Approved"
    admission = _job(client, "/api/actions/admit", {"run_id": run_id})
    action_id = admission["result"]["action"]["action_id"]
    authorization = _job(
        client,
        "/api/actions/authorize",
        {"action_id": action_id, "confirmation": f"AUTHORIZE {action_id}"},
    )
    assert authorization["result"]["status"] == "Authorized"

    original = LocalIncidentTicketStore.create_or_get

    def commit_then_lose_ack(self, **kwargs):
        original(self, **kwargs)
        raise StoreOutcomeUnknown(
            "injected committed effect with lost receipt acknowledgement"
        )

    monkeypatch.setattr(LocalIncidentTicketStore, "create_or_get", commit_then_lose_ack)
    execution = _job(
        client,
        "/api/actions/execute",
        {"action_id": action_id, "confirmation": f"EXECUTE {action_id}"},
    )
    assert execution["result"]["status"] == "Unknown"
    assert execution["result"]["effect_observed_without_runtime_receipt"] is True
    status = client.get(f"/api/status?action_id={action_id}").json()
    assert status["status"] == "Unknown"
    assert status["effect_observed"] is True
    assert status["receipt_verified"] is False
    assert client.get("/api/tickets").json()["count"] == 1

    retry = _job(
        client,
        "/api/actions/execute",
        {"action_id": action_id, "confirmation": f"EXECUTE {action_id}"},
    )
    assert retry["state"] == "failed"
    assert client.get("/api/tickets").json()["count"] == 1


def test_invalid_incident_and_missing_services_fail_closed(console, monkeypatch):
    client = console["client"]
    invalid = _job(
        client, "/api/investigations", {"incident_id": "incident-does-not-exist"}
    )
    assert invalid["state"] == "failed"
    assert invalid["error"]["code"] == "not_found"

    def no_database():
        raise DatabaseUnavailable("injected unavailable local FalkorDB")

    console["server"].service.store_factory = no_database
    unavailable = client.get("/api/catalog")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "database_unavailable"

    class MissingModel:
        def __call__(self):
            from incidentgraph_agent.agent import ModelUnavailable

            raise ModelUnavailable("injected unavailable local Ollama")

    console["server"].service.store_factory = lambda: FalkorStore(
        console["store_settings"]
    )
    console["server"].service.provider_factory = MissingModel()
    missing_model = _job(client, "/api/investigations", {"incident_id": INCIDENT_ID})
    assert missing_model["state"] == "failed"
    assert missing_model["error"]["code"] == "model_unavailable"


def test_one_web_process_locks_its_action_home(console):
    with pytest.raises(WebConsoleError) as error:
        create_server(console["home"], 0)
    assert error.value.code == "action_home_in_use"
