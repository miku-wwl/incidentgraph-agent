from __future__ import annotations

import json
from contextlib import nullcontext

import pytest

import incidentgraph_agent.cli as cli
import incidentgraph_agent.ticket_actions as ticket_actions


def _mock_action_command(monkeypatch, command, callback):
    monkeypatch.setattr(cli.Settings, "from_env", lambda: object())
    monkeypatch.setattr(cli, "FalkorStore", lambda _settings: nullcontext(object()))
    monkeypatch.setattr(ticket_actions, command, callback)


def test_action_init_emits_structured_json_without_exposing_capability(
    tmp_path, capsys
):
    home = tmp_path / "cli-action-home"
    exit_code = cli.main(["action", "init", "--home", str(home)])
    output = capsys.readouterr().out
    result = json.loads(output)

    assert exit_code == 0
    assert result["status"] == "initialized"
    assert result["action_home"] == str(home.resolve())
    assert result["execution_budget_seconds"] == 5
    assert "token" not in output.lower()
    assert (home / "gate.sqlite3").is_file()
    assert (home / "tickets.sqlite3").is_file()
    assert (home / "snapshots.sqlite3").is_file()


@pytest.mark.parametrize(
    ("action_command", "workflow_name", "identity", "challenge", "phrase"),
    [
        (
            "approve",
            "decide_run",
            "run-123",
            "plan-digest-123",
            "APPROVE plan-digest-123",
        ),
        (
            "authorize",
            "authorize_action",
            "action-123",
            "action-123",
            "AUTHORIZE action-123",
        ),
        (
            "execute",
            "execute_action",
            "action-123",
            "action-123",
            "EXECUTE action-123",
        ),
    ],
)
def test_action_cli_displays_complete_confirmation_phrase(
    tmp_path,
    monkeypatch,
    capsys,
    action_command,
    workflow_name,
    identity,
    challenge,
    phrase,
):
    prompts = []

    def fake_workflow(*_args, confirm, **_kwargs):
        return {"status": "accepted", "typed": confirm(challenge)}

    _mock_action_command(monkeypatch, workflow_name, fake_workflow)
    monkeypatch.setattr(
        "builtins.input", lambda prompt: (prompts.append(prompt) or phrase)
    )
    exit_code = cli.main(
        [
            "action",
            action_command,
            identity,
            "--home",
            str(tmp_path / "isolated-home"),
        ]
    )

    assert exit_code == 0
    assert prompts == [f"Type exactly:\n{phrase}\n"]
    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "accepted", "typed": phrase}


@pytest.mark.parametrize("cancellation", [EOFError, KeyboardInterrupt])
def test_cli_confirmation_cancellation_is_controlled(
    tmp_path, monkeypatch, capsys, cancellation
):
    def fake_decide(_home, _run_id, *, confirm, **_kwargs):
        return {"status": "accepted", "typed": confirm("plan-digest-123")}

    _mock_action_command(monkeypatch, "decide_run", fake_decide)

    def cancel(_prompt):
        raise cancellation

    monkeypatch.setattr("builtins.input", cancel)
    exit_code = cli.main(
        ["action", "approve", "run-123", "--home", str(tmp_path / "isolated-home")]
    )

    assert exit_code == 2
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "error": {
            "code": "operator_confirmation_cancelled",
            "message": "Confirmation was cancelled before the lifecycle operation was authorized.",
        }
    }
