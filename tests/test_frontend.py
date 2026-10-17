"""Run the studio UI behavioral harness when the development Node runtime exists."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_studio_ui_behaviors() -> None:
  node = shutil.which("node")
  if node is None:
    pytest.skip("Node 22+ is required for studio UI behavioral tests")
  repository = Path(__file__).resolve().parents[1]
  test_files = sorted(
    path.relative_to(repository).as_posix()
    for path in (repository / "tests/frontend").glob("*.test.cjs")
  )
  result = subprocess.run(
    [node, "--test", *test_files],
    cwd=repository,
    check=False,
    capture_output=True,
    text=True,
  )
  if result.returncode != 0:
    pytest.fail(f"Studio UI tests failed:\n{result.stdout}\n{result.stderr}")
