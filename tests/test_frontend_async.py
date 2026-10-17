from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_frontend_async_race_harness():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the frontend race harness.")

    harness = Path(__file__).with_name("frontend_async_race_harness.cjs")
    result = subprocess.run(
        [node, str(harness)],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "all 9 scenarios passed" in result.stdout
