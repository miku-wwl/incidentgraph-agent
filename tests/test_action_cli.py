from __future__ import annotations

import json

from incidentgraph_agent.cli import main


def test_action_init_emits_structured_json_without_exposing_capability(
    tmp_path, capsys
):
    home = tmp_path / "cli-action-home"
    exit_code = main(["action", "init", "--home", str(home)])
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
