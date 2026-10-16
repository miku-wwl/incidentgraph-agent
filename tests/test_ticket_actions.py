from __future__ import annotations

import asyncio
import json
import signal
import subprocess
import sys
from dataclasses import replace
from uuid import uuid4

import pytest

import incidentgraph_agent.cli as cli
from incidentgraph_agent.action_evidence import (
    build_snapshot_from_agent_result,
    snapshot_from_graph_evidence,
)
from incidentgraph_agent.agent import AgentLoop
from incidentgraph_agent.action_store import (
    EffectPayloadConflict,
    LocalIncidentTicketStore,
    StoreIdentityMismatch,
    StoreOutcomeUnknown,
)
from agentic_intent_gate.runtime import ActionCommand
from incidentgraph_agent.ticket_actions import (
    ARGUMENT_SCHEMA,
    ActionWorkflowError,
    ActionWorkspace,
    action_status,
    admit_run,
    authorize_action,
    create_action_proposal,
    decide_run,
    execute_action,
    list_tickets,
    open_action_runtime,
    provision_action_workspace,
    review_run,
    _argument_semantics,
)
from incidentgraph_agent.graph_tools import GraphToolRegistry
from incidentgraph_agent.repository import IncidentRepository


INCIDENT_ID = "incident-payment-001"


def _new_workspace(tmp_path):
    home = tmp_path / "isolated-action-home"
    provision_action_workspace(home)
    return home


class _DecisionProvider:
    model_name = "phase3-test-provider"

    def __init__(self):
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
                    "answer": "Graph evidence is complete; causality remains unproven.",
                    "evidence_ids": [f"incident:{INCIDENT_ID}"],
                }
            ),
        ]

    def complete(self, _messages):
        return self.decisions.pop(0)


def _snapshot(repository):
    result = AgentLoop(
        _DecisionProvider(),
        GraphToolRegistry(IncidentRepository(repository.store)),
    ).run(f"Investigate {INCIDENT_ID} and explain which services may be affected.")
    return build_snapshot_from_agent_result(
        result,
        incident_id=INCIDENT_ID,
        graph_identity=repository.store.settings.graph_name,
    )


def _proposal(home, store, repository):
    return create_action_proposal(home, _snapshot(repository), graph_store=store)


def _pending_action(home, store, repository):
    proposal = _proposal(home, store, repository)
    run_id = proposal["run"]["run_id"]
    decide_run(
        home,
        run_id,
        approve=True,
        confirm=_confirm("APPROVE"),
        graph_store=store,
    )
    return admit_run(home, run_id, graph_store=store)["action"]["action_id"]


def _confirm(prefix):
    return lambda value: f"{prefix} {value}"


def test_operator_lifecycle_uses_distinct_gates_and_persists_one_ticket(
    tmp_path, store, repository
):
    home = _new_workspace(tmp_path)
    proposal = _proposal(home, store, repository)
    run_id = proposal["run"]["run_id"]
    assert proposal["status"] == "awaiting_approval"
    assert proposal["ticket_count"] == 0
    plan = proposal["run"]["plan"]
    assert plan["risk_class"] == "SIDE_EFFECT"
    assert plan["target"] == ActionWorkspace.load(home).binding.target
    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["tool_name"] == "create_local_incident_ticket"
    assert plan["steps"][0]["arguments"] == {
        "incident_id": INCIDENT_ID,
        "evidence_digest": proposal["evidence_digest"],
        "snapshot_observed_at": proposal["snapshot_observed_at"],
        "affected_service_count": 1,
    }

    reviewed = review_run(home, run_id)
    assert reviewed["plan_digest"] == proposal["plan_digest"]
    assert reviewed["evidence_digest"] == proposal["evidence_digest"]
    assert reviewed["snapshot_fresh"] is True
    assert reviewed["risk_class"] == "SIDE_EFFECT"

    with pytest.raises(ActionWorkflowError):
        admit_run(home, run_id, graph_store=store)
    assert list_tickets(home)["count"] == 0

    approved = decide_run(
        home,
        run_id,
        approve=True,
        confirm=_confirm("APPROVE"),
        graph_store=store,
    )
    assert approved["status"] == "Approved"

    admitted = admit_run(home, run_id, graph_store=store)
    action_id = admitted["action"]["action_id"]
    assert admitted["status"] == "Pending"
    assert admitted["ticket_count"] == 0

    with pytest.raises(ActionWorkflowError):
        execute_action(
            home,
            action_id,
            confirm=_confirm("EXECUTE"),
            graph_store=store,
        )
    assert list_tickets(home)["count"] == 0

    authorized = authorize_action(
        home,
        action_id,
        confirm=_confirm("AUTHORIZE"),
        graph_store=store,
    )
    assert authorized["status"] == "Authorized"

    result = execute_action(
        home,
        action_id,
        confirm=_confirm("EXECUTE"),
        graph_store=store,
    )
    assert result["status"] == "Succeeded"
    assert result["receipt_verified"] is True
    assert result["ticket"]["incident_id"] == INCIDENT_ID
    assert result["ticket"]["evidence_digest"] == proposal["evidence_digest"]

    persisted = list_tickets(home)
    assert persisted["count"] == 1
    assert persisted["tickets"][0]["receipt_id"] == result["ticket"]["receipt_id"]
    status = action_status(home, action_id)
    assert status["status"] == "Succeeded"
    assert status["receipt_verified"] is True

    restart_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,sys; "
            "from incidentgraph_agent.ticket_actions import action_status, "
            "list_tickets, execute_action, ActionWorkflowError; "
            "home,action_id=sys.argv[1:]; "
            "status=action_status(home,action_id); "
            "exec_result=None; "
            "try_exec=execute_action; "
            "\ntry:\n try_exec(home,action_id,confirm=lambda _: 'EXECUTE '+action_id,graph_store=None)\n"
            "except ActionWorkflowError as exc:\n exec_result=exc.code\n"
            "print(json.dumps({'status':status['status'],'ticket_count':list_tickets(home)['count'],'replay_denied':exec_result}))",
            str(home),
            action_id,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    restarted = json.loads(restart_probe.stdout)
    assert restarted == {
        "status": "Succeeded",
        "ticket_count": 1,
        "replay_denied": "action_state_invalid",
    }

    with pytest.raises(ActionWorkflowError):
        execute_action(
            home,
            action_id,
            confirm=_confirm("EXECUTE"),
            graph_store=store,
        )
    assert list_tickets(home)["count"] == 1


def test_changed_graph_evidence_blocks_approval_and_creates_no_ticket(
    tmp_path, store, repository
):
    home = _new_workspace(tmp_path)
    proposal = _proposal(home, store, repository)
    store.write(
        "MATCH (i:Incident {id: $incident_id}) SET i.summary = $summary",
        {"incident_id": INCIDENT_ID, "summary": "Changed during review"},
    )

    with pytest.raises(ActionWorkflowError) as error:
        decide_run(
            home,
            proposal["run"]["run_id"],
            approve=True,
            confirm=_confirm("APPROVE"),
            graph_store=store,
        )
    assert error.value.code == "evidence_changed"
    assert list_tickets(home)["count"] == 0


def test_snapshot_from_another_graph_identity_is_rejected(tmp_path, store, repository):
    home = _new_workspace(tmp_path)
    snapshot = snapshot_from_graph_evidence(
        repository.investigate(INCIDENT_ID),
        incident_id=INCIDENT_ID,
        graph_identity="incidentgraph_other_dataset",
    )

    with pytest.raises(ActionWorkflowError) as error:
        create_action_proposal(home, snapshot, graph_store=store)
    assert error.value.code == "evidence_changed"
    assert list_tickets(home)["count"] == 0


def test_expired_snapshot_cannot_create_an_action_plan(tmp_path, store, repository):
    from datetime import UTC, datetime, timedelta

    home = _new_workspace(tmp_path)
    snapshot = snapshot_from_graph_evidence(
        repository.investigate(INCIDENT_ID),
        incident_id=INCIDENT_ID,
        graph_identity=repository.store.settings.graph_name,
        observed_at=(datetime.now(UTC) - timedelta(minutes=11))
        .isoformat()
        .replace("+00:00", "Z"),
    )

    with pytest.raises(ActionWorkflowError) as error:
        create_action_proposal(home, snapshot, graph_store=store)
    assert error.value.code == "evidence_unavailable"
    assert list_tickets(home)["count"] == 0


def test_flat_action_arguments_reject_boolean_count_and_extra_fields():
    valid = {
        "incident_id": INCIDENT_ID,
        "evidence_digest": "a" * 64,
        "snapshot_observed_at": "2026-10-16T00:00:00.000000Z",
        "affected_service_count": 1,
    }
    assert (
        _argument_semantics(
            valid, "incidentgraph-ticket-store-00000000-0000-4000-8000-000000000000"
        )
        is None
    )
    assert set(ARGUMENT_SCHEMA["properties"]) == set(valid)

    boolean_count = valid | {"affected_service_count": True}
    assert _argument_semantics(boolean_count, "trusted-target") is not None
    extra_field = valid | {"execution_scope": str(uuid4())}
    assert _argument_semantics(extra_field, "trusted-target") is not None


def test_changed_graph_evidence_after_authorization_blocks_execution(
    tmp_path, store, repository
):
    home = _new_workspace(tmp_path)
    proposal = _proposal(home, store, repository)
    run_id = proposal["run"]["run_id"]
    decide_run(
        home,
        run_id,
        approve=True,
        confirm=_confirm("APPROVE"),
        graph_store=store,
    )
    admitted = admit_run(home, run_id, graph_store=store)
    action_id = admitted["action"]["action_id"]
    authorize_action(
        home,
        action_id,
        confirm=_confirm("AUTHORIZE"),
        graph_store=store,
    )
    store.write(
        "MATCH (i:Incident {id: $incident_id}) SET i.summary = $summary",
        {"incident_id": INCIDENT_ID, "summary": "Changed before dispatch"},
    )

    with pytest.raises(ActionWorkflowError) as error:
        execute_action(
            home,
            action_id,
            confirm=_confirm("EXECUTE"),
            graph_store=store,
        )
    assert error.value.code == "evidence_changed"
    assert list_tickets(home)["count"] == 0
    assert action_status(home, action_id)["status"] == "Authorized"


def test_reject_is_terminal_and_never_admits_an_action(tmp_path, store, repository):
    home = _new_workspace(tmp_path)
    proposal = _proposal(home, store, repository)
    rejected = decide_run(
        home,
        proposal["run"]["run_id"],
        approve=False,
        confirm=_confirm("REJECT"),
        graph_store=store,
    )
    assert rejected["status"] == "Rejected"
    with pytest.raises(ActionWorkflowError):
        admit_run(home, proposal["run"]["run_id"], graph_store=store)
    assert list_tickets(home)["count"] == 0


def test_action_workspace_refuses_modified_binding_identity(tmp_path):
    home = _new_workspace(tmp_path)
    identity = home / "identity.json"
    document = __import__("json").loads(identity.read_text(encoding="utf-8"))
    document["binding"]["target"] = "attacker-target"
    identity.write_text(__import__("json").dumps(document), encoding="utf-8")

    with pytest.raises(ActionWorkflowError):
        ActionWorkspace.load(home)


def test_invalid_capability_file_is_rejected_before_runtime_use(tmp_path):
    home = _new_workspace(tmp_path)
    (home / "capability").write_text("not-a-capability", encoding="ascii")
    with pytest.raises(ActionWorkflowError):
        ActionWorkspace.load(home)


def test_missing_runtime_ledger_is_not_silently_recreated(tmp_path):
    home = _new_workspace(tmp_path)
    gate_path = home / "gate.sqlite3"
    gate_path.unlink()

    with pytest.raises(ActionWorkflowError):
        review_run(home, str(uuid4()))
    assert not gate_path.exists()


def test_scope_rebinding_does_not_reuse_old_ticket_store(tmp_path):
    home = _new_workspace(tmp_path)
    identity_path = home / "identity.json"
    document = json.loads(identity_path.read_text(encoding="utf-8"))
    document["binding"]["execution_scope"] = str(uuid4())
    identity_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(StoreIdentityMismatch):
        with open_action_runtime(home):
            pass


@pytest.mark.parametrize(
    "bad_confirmation", ["missing_prefix", "wrong_digest", "empty"]
)
def test_invalid_plan_confirmation_does_not_advance_run(
    tmp_path, store, repository, bad_confirmation
):
    home = _new_workspace(tmp_path)
    proposal = _proposal(home, store, repository)

    def response(challenge):
        if bad_confirmation == "missing_prefix":
            return challenge
        if bad_confirmation == "wrong_digest":
            digest = "0" * 64 if challenge != "0" * 64 else "1" * 64
            return f"APPROVE {digest}"
        return ""

    with pytest.raises(ActionWorkflowError) as error:
        decide_run(
            home,
            proposal["run"]["run_id"],
            approve=True,
            confirm=response,
            graph_store=store,
        )
    assert error.value.code == "operator_confirmation_failed"
    assert review_run(home, proposal["run"]["run_id"])["status"] == "AwaitingApproval"
    assert list_tickets(home)["count"] == 0


@pytest.mark.parametrize(
    "bad_confirmation", ["missing_prefix", "wrong_action_id", "empty"]
)
def test_invalid_authorization_confirmation_does_not_authorize_or_create_ticket(
    tmp_path, store, repository, bad_confirmation
):
    home = _new_workspace(tmp_path)
    action_id = _pending_action(home, store, repository)

    def response(challenge):
        if bad_confirmation == "missing_prefix":
            return challenge
        if bad_confirmation == "wrong_action_id":
            return "AUTHORIZE another-action"
        return ""

    with pytest.raises(ActionWorkflowError) as error:
        authorize_action(home, action_id, confirm=response, graph_store=store)
    assert error.value.code == "operator_confirmation_failed"
    assert action_status(home, action_id)["status"] == "Pending"
    assert list_tickets(home)["count"] == 0


@pytest.mark.parametrize(
    "bad_confirmation", ["missing_prefix", "wrong_action_id", "empty"]
)
def test_invalid_execution_confirmation_does_not_execute_or_create_ticket(
    tmp_path, store, repository, bad_confirmation
):
    home = _new_workspace(tmp_path)
    action_id = _pending_action(home, store, repository)
    authorize_action(
        home,
        action_id,
        confirm=_confirm("AUTHORIZE"),
        graph_store=store,
    )

    def response(challenge):
        if bad_confirmation == "missing_prefix":
            return challenge
        if bad_confirmation == "wrong_action_id":
            return "EXECUTE another-action"
        return ""

    with pytest.raises(ActionWorkflowError) as error:
        execute_action(home, action_id, confirm=response, graph_store=store)
    assert error.value.code == "operator_confirmation_failed"
    assert action_status(home, action_id)["status"] == "Authorized"
    assert list_tickets(home)["count"] == 0


def test_cancelled_execution_confirmation_does_not_dispatch_action(
    tmp_path, store, repository
):
    home = _new_workspace(tmp_path)
    proposal = _proposal(home, store, repository)
    run_id = proposal["run"]["run_id"]
    decide_run(
        home,
        run_id,
        approve=True,
        confirm=_confirm("APPROVE"),
        graph_store=store,
    )
    action_id = admit_run(home, run_id, graph_store=store)["action"]["action_id"]
    authorize_action(
        home,
        action_id,
        confirm=_confirm("AUTHORIZE"),
        graph_store=store,
    )

    def cancel(_challenge):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute_action(home, action_id, confirm=cancel, graph_store=store)

    assert action_status(home, action_id)["status"] == "Authorized"
    assert list_tickets(home)["count"] == 0


def test_effect_key_is_payload_bound_and_wrong_store_identity_fails(tmp_path):
    home = _new_workspace(tmp_path)
    workspace = ActionWorkspace.load(home)
    store = LocalIncidentTicketStore(
        workspace.ticket_path, workspace.ticket_store_id, workspace.binding
    )
    effect_key = str(uuid4())
    payload_digest = "a" * 64
    receipt = store.create_or_get(
        incident_id=INCIDENT_ID,
        evidence_digest="b" * 64,
        payload_digest=payload_digest,
        effect_key=effect_key,
    )
    assert store.find_effect(effect_key, payload_digest) == receipt
    assert (
        store.create_or_get(
            incident_id=INCIDENT_ID,
            evidence_digest="b" * 64,
            payload_digest=payload_digest,
            effect_key=effect_key,
        )
        == receipt
    )
    assert store.count() == 1
    with pytest.raises(EffectPayloadConflict):
        store.find_effect(effect_key, "c" * 64)
    with pytest.raises(StoreIdentityMismatch):
        LocalIncidentTicketStore(workspace.ticket_path, str(uuid4()), workspace.binding)


def test_lost_ticket_commit_ack_is_unknown_and_never_retried(
    tmp_path, store, repository, monkeypatch
):
    home = _new_workspace(tmp_path)
    proposal = _proposal(home, store, repository)
    run_id = proposal["run"]["run_id"]
    decide_run(
        home,
        run_id,
        approve=True,
        confirm=_confirm("APPROVE"),
        graph_store=store,
    )
    admitted = admit_run(home, run_id, graph_store=store)
    action_id = admitted["action"]["action_id"]
    authorize_action(
        home,
        action_id,
        confirm=_confirm("AUTHORIZE"),
        graph_store=store,
    )

    original = LocalIncidentTicketStore.create_or_get

    def commit_then_lose_ack(self, **kwargs):
        original(self, **kwargs)
        raise StoreOutcomeUnknown("injected lost acknowledgment after durable commit")

    monkeypatch.setattr(LocalIncidentTicketStore, "create_or_get", commit_then_lose_ack)
    result = execute_action(
        home,
        action_id,
        confirm=_confirm("EXECUTE"),
        graph_store=store,
    )
    assert result["status"] == "Unknown"
    assert result["effect_observed_without_runtime_receipt"] is True
    assert list_tickets(home)["count"] == 1
    status = action_status(home, action_id)
    assert status["status"] == "Unknown"
    assert status["effect_observed"] is True

    with pytest.raises(ActionWorkflowError):
        execute_action(
            home,
            action_id,
            confirm=_confirm("EXECUTE"),
            graph_store=store,
        )
    assert list_tickets(home)["count"] == 1


@pytest.mark.parametrize(
    "readback_error", [StoreIdentityMismatch, EffectPayloadConflict]
)
def test_post_commit_readback_rejection_is_unknown_and_never_replayed(
    tmp_path, store, repository, monkeypatch, readback_error
):
    home = _new_workspace(tmp_path)
    action_id = _pending_action(home, store, repository)
    authorize_action(
        home,
        action_id,
        confirm=_confirm("AUTHORIZE"),
        graph_store=store,
    )
    original = LocalIncidentTicketStore.find_effect
    calls = 0

    def reject_post_commit_readback(self, effect_key, payload_digest):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise readback_error("injected post-commit readback rejection")
        return original(self, effect_key, payload_digest)

    monkeypatch.setattr(
        LocalIncidentTicketStore, "find_effect", reject_post_commit_readback
    )
    result = execute_action(
        home,
        action_id,
        confirm=_confirm("EXECUTE"),
        graph_store=store,
    )

    assert calls >= 3
    assert result["status"] == "Unknown"
    assert result["effect_observed_without_runtime_receipt"] is True
    assert list_tickets(home)["count"] == 1
    persisted = action_status(home, action_id)
    assert persisted["status"] == "Unknown"
    assert persisted["effect_observed"] is True
    with pytest.raises(ActionWorkflowError):
        execute_action(
            home,
            action_id,
            confirm=_confirm("EXECUTE"),
            graph_store=store,
        )
    assert list_tickets(home)["count"] == 1


@pytest.mark.parametrize(
    "pre_effect_error", [StoreIdentityMismatch, EffectPayloadConflict]
)
def test_pre_effect_store_rejection_remains_confirmed_failure(
    tmp_path, store, repository, monkeypatch, pre_effect_error
):
    home = _new_workspace(tmp_path)
    action_id = _pending_action(home, store, repository)
    authorize_action(
        home,
        action_id,
        confirm=_confirm("AUTHORIZE"),
        graph_store=store,
    )

    def reject_before_create(self, _effect_key, _payload_digest):
        raise pre_effect_error("injected pre-effect validation rejection")

    def unexpected_create(*_args, **_kwargs):
        pytest.fail("ticket insertion must not run after pre-effect rejection")

    monkeypatch.setattr(LocalIncidentTicketStore, "find_effect", reject_before_create)
    monkeypatch.setattr(LocalIncidentTicketStore, "create_or_get", unexpected_create)
    result = execute_action(
        home,
        action_id,
        confirm=_confirm("EXECUTE"),
        graph_store=store,
    )
    assert result["status"] == "Failed"
    assert list_tickets(home)["count"] == 0


def test_existing_pre_effect_payload_conflict_is_rejected_without_new_ticket(
    tmp_path, store, repository
):
    home = _new_workspace(tmp_path)
    action_id = _pending_action(home, store, repository)
    authorize_action(
        home,
        action_id,
        confirm=_confirm("AUTHORIZE"),
        graph_store=store,
    )
    workspace = ActionWorkspace.load(home)
    with open_action_runtime(home, graph_store=store) as (
        runtime,
        principal,
        _snapshots,
        _tickets,
    ):
        action = runtime.gateway.get(action_id, principal)
    tickets = LocalIncidentTicketStore(
        workspace.ticket_path, workspace.ticket_store_id, workspace.binding
    )
    existing = tickets.create_or_get(
        incident_id=INCIDENT_ID,
        evidence_digest="b" * 64,
        payload_digest="c" * 64,
        effect_key=action.effect_key,
    )

    result = execute_action(
        home,
        action_id,
        confirm=_confirm("EXECUTE"),
        graph_store=store,
    )

    assert result["status"] == "Failed"
    persisted = list_tickets(home)
    assert persisted["count"] == 1
    assert persisted["tickets"][0]["ticket_id"] == existing.ticket_id
    assert persisted["tickets"][0]["receipt_id"] == existing.receipt_id


def test_shallow_snapshot_is_rejected_at_action_proposal_entrypoint(
    tmp_path, store, repository
):
    home = _new_workspace(tmp_path)
    shallow_evidence = repository.investigate(INCIDENT_ID, max_depth=1)
    snapshot = snapshot_from_graph_evidence(
        shallow_evidence,
        incident_id=INCIDENT_ID,
        graph_identity=store.settings.graph_name,
    )
    with pytest.raises(ActionWorkflowError) as error:
        create_action_proposal(home, snapshot, graph_store=store)
    assert error.value.code == "evidence_changed"
    assert list_tickets(home)["count"] == 0


def test_cli_reports_sigint_after_committed_effect_as_ambiguous(
    tmp_path, store, repository, monkeypatch, capsys
):
    monkeypatch.setattr(cli.Settings, "from_env", lambda: store.settings)
    home = _new_workspace(tmp_path)
    action_id = _pending_action(home, store, repository)
    authorize_action(
        home,
        action_id,
        confirm=_confirm("AUTHORIZE"),
        graph_store=store,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: f"EXECUTE {action_id}")
    original = LocalIncidentTicketStore.create_or_get

    def commit_then_sigint(self, **kwargs):
        receipt = original(self, **kwargs)
        signal.raise_signal(signal.SIGINT)
        return receipt

    monkeypatch.setattr(LocalIncidentTicketStore, "create_or_get", commit_then_sigint)
    exit_code = cli.main(["action", "execute", action_id, "--home", str(home)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["error"]["code"] == "execution_interrupted"
    assert payload["error"]["action_id"] == action_id
    assert payload["error"]["action_home"] == str(home.resolve())
    assert payload["error"]["inspect_action"] == [
        "incidentgraph",
        "action",
        "status",
        action_id,
        "--home",
        str(home.resolve()),
    ]
    assert payload["error"]["inspect_tickets"] == [
        "incidentgraph",
        "action",
        "tickets",
        "--home",
        str(home.resolve()),
    ]
    assert "may have occurred" in payload["error"]["message"]
    assert "Do not retry" in payload["error"]["message"]
    assert action_status(home, action_id)["status"] == "Unknown"
    assert action_status(home, action_id)["effect_observed"] is True
    assert list_tickets(home)["count"] == 1
    with pytest.raises(ActionWorkflowError):
        execute_action(
            home,
            action_id,
            confirm=_confirm("EXECUTE"),
            graph_store=store,
        )
    assert list_tickets(home)["count"] == 1


@pytest.mark.parametrize("cancellation", [EOFError, KeyboardInterrupt])
def test_cli_execute_prompt_cancellation_never_dispatches(
    tmp_path, store, repository, monkeypatch, capsys, cancellation
):
    monkeypatch.setattr(cli.Settings, "from_env", lambda: store.settings)
    home = _new_workspace(tmp_path)
    action_id = _pending_action(home, store, repository)
    authorize_action(
        home,
        action_id,
        confirm=_confirm("AUTHORIZE"),
        graph_store=store,
    )

    def cancel_at_prompt(_prompt):
        raise cancellation

    monkeypatch.setattr("builtins.input", cancel_at_prompt)
    exit_code = cli.main(["action", "execute", action_id, "--home", str(home)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["error"]["code"] == "operator_confirmation_cancelled"
    assert action_status(home, action_id)["status"] == "Authorized"
    assert list_tickets(home)["count"] == 0


def test_cli_execution_interrupt_before_dispatch_is_conservative(
    tmp_path, store, repository, monkeypatch, capsys
):
    home = _new_workspace(tmp_path)
    action_id = _pending_action(home, store, repository)
    authorize_action(
        home,
        action_id,
        confirm=_confirm("AUTHORIZE"),
        graph_store=store,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: f"EXECUTE {action_id}")

    def interrupt_before_dispatch(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "incidentgraph_agent.ticket_actions._validate_snapshot_current",
        interrupt_before_dispatch,
    )
    exit_code = cli.main(["action", "execute", action_id, "--home", str(home)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["error"]["code"] == "execution_interrupted"
    assert payload["error"]["action_id"] == action_id
    assert "may have occurred" in payload["error"]["message"]
    assert action_status(home, action_id)["status"] == "Authorized"
    assert list_tickets(home)["count"] == 0


def test_concurrent_gateway_execution_creates_one_ticket(tmp_path, store, repository):
    home = _new_workspace(tmp_path)
    proposal = _proposal(home, store, repository)
    run_id = proposal["run"]["run_id"]
    decide_run(
        home,
        run_id,
        approve=True,
        confirm=_confirm("APPROVE"),
        graph_store=store,
    )
    action_id = admit_run(home, run_id, graph_store=store)["action"]["action_id"]
    authorize_action(
        home,
        action_id,
        confirm=_confirm("AUTHORIZE"),
        graph_store=store,
    )

    async def race(runtime, principal):
        action = runtime.gateway.get(action_id, principal)
        commands = [
            ActionCommand(action_id, action.version, str(uuid4())) for _ in range(2)
        ]
        return await asyncio.gather(
            *(runtime.gateway.execute(command, principal) for command in commands),
            return_exceptions=True,
        )

    with open_action_runtime(home, graph_store=store) as (
        runtime,
        principal,
        _snapshots,
        _tickets,
    ):
        results = asyncio.run(race(runtime, principal))
    successes = [
        result
        for result in results
        if not isinstance(result, Exception)
        and result.action.state.value == "Succeeded"
    ]
    assert len(successes) == 1
    assert list_tickets(home)["count"] == 1


def test_two_distinct_explicitly_authorized_actions_have_distinct_receipts(
    tmp_path, store, repository
):
    home = _new_workspace(tmp_path)
    receipts = []
    for _ in range(2):
        proposal = _proposal(home, store, repository)
        run_id = proposal["run"]["run_id"]
        decide_run(
            home,
            run_id,
            approve=True,
            confirm=_confirm("APPROVE"),
            graph_store=store,
        )
        action_id = admit_run(home, run_id, graph_store=store)["action"]["action_id"]
        authorize_action(
            home,
            action_id,
            confirm=_confirm("AUTHORIZE"),
            graph_store=store,
        )
        output = execute_action(
            home,
            action_id,
            confirm=_confirm("EXECUTE"),
            graph_store=store,
        )
        assert output["status"] == "Succeeded"
        receipts.append(output["ticket"]["receipt_id"])
    assert len(set(receipts)) == 2
    assert list_tickets(home)["count"] == 2


def test_malformed_receipt_is_not_reported_as_success(
    tmp_path, store, repository, monkeypatch
):
    home = _new_workspace(tmp_path)
    proposal = _proposal(home, store, repository)
    run_id = proposal["run"]["run_id"]
    decide_run(
        home,
        run_id,
        approve=True,
        confirm=_confirm("APPROVE"),
        graph_store=store,
    )
    action_id = admit_run(home, run_id, graph_store=store)["action"]["action_id"]
    authorize_action(
        home,
        action_id,
        confirm=_confirm("AUTHORIZE"),
        graph_store=store,
    )
    original = LocalIncidentTicketStore.get_receipt

    def malformed_receipt(self, receipt_id):
        receipt = original(self, receipt_id)
        return replace(receipt, incident_id="incident-forged") if receipt else None

    monkeypatch.setattr(LocalIncidentTicketStore, "get_receipt", malformed_receipt)
    with pytest.raises(ActionWorkflowError) as error:
        execute_action(
            home,
            action_id,
            confirm=_confirm("EXECUTE"),
            graph_store=store,
        )
    assert error.value.code == "receipt_verification_failed"
    assert list_tickets(home)["count"] == 1
