"""Run the replay UI behavioral harness when the development Node runtime exists."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_replay_ui_behaviors() -> None:
  node = shutil.which("node")
  if node is None:
    pytest.skip("Node 22+ is required for replay UI behavioral tests")
  result = subprocess.run(
    [node, "--test", "tests/frontend/audit.test.cjs"],
    cwd=Path(__file__).resolve().parents[1],
    check=False,
    capture_output=True,
    text=True,
  )
  if result.returncode != 0:
    pytest.fail(f"Replay UI tests failed:\n{result.stdout}\n{result.stderr}")
