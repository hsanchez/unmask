import pytest

from unmask.backend import GeminiBackend
from unmask.config import TargetConfig


def _target(project: str | None) -> TargetConfig:
  return TargetConfig(
    project=project,
    location="global",
    judge_model="gemini-2.5-flash",
    thinking_budget=None,
  )


class TestGeminiBackendInit:
  def test_requires_project(self, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    with pytest.raises(ValueError, match="target.project"):
      _ = GeminiBackend(_target(project=None))

  def test_falls_back_when_genai_is_not_installed(self) -> None:
    with pytest.raises(RuntimeError, match="Gemini support is not installed"):
      _ = GeminiBackend(_target(project="test-project"))
