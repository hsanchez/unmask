"""LLM judge backends used to extract a symbolic model from a guardrail."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
  from types import ModuleType

  from .config import TargetConfig


class JudgeBackend(Protocol):
  """A model that can be prompted to return a single JSON response."""

  def judge(self, prompt: str, *, max_tokens: int = 8192) -> str: ...


class _Response(Protocol):
  text: object


class _Models(Protocol):
  def generate_content(
    self, *, model: str, contents: str, config: object
  ) -> _Response: ...


class _Client(Protocol):
  models: _Models


class _ClientFactory(Protocol):
  def __call__(self, *, vertexai: bool, project: str, location: str) -> _Client: ...


class _GenaiModule(Protocol):
  Client: _ClientFactory


class _ThinkingConfigFactory(Protocol):
  def __call__(self, *, thinking_budget: int) -> object: ...


class _GenerateContentConfigFactory(Protocol):
  def __call__(
    self,
    *,
    temperature: int,
    max_output_tokens: int,
    response_mime_type: str,
    thinking_config: object | None,
  ) -> object: ...


class _TypesModule(Protocol):
  ThinkingConfig: _ThinkingConfigFactory
  GenerateContentConfig: _GenerateContentConfigFactory


def _load_optional_module(name: str) -> ModuleType:
  return import_module(name)


class GeminiBackend:
  """Judges prompts with Gemini on Vertex AI via the `google-genai` SDK."""

  def __init__(self, config: TargetConfig) -> None:
    """
    Raises:
      ValueError: if `config.project` is unset and `GOOGLE_CLOUD_PROJECT` is
        not in the environment.
      RuntimeError: if the optional `google-genai` dependency is not installed.
    """
    if not config.project:
      raise ValueError(
        "target.project or GOOGLE_CLOUD_PROJECT is required for neural extraction"
      )

    try:
      genai: _GenaiModule = cast(
        "_GenaiModule", cast("object", _load_optional_module(name="google.genai"))
      )
      types: _TypesModule = cast(
        "_TypesModule",
        cast("object", _load_optional_module(name="google.genai.types")),
      )
    except ImportError as exc:
      raise RuntimeError(
        "Gemini support is not installed; run 'uv sync --extra gemini'"
      ) from exc

    self._types: _TypesModule = types
    self._client: _Client = genai.Client(
      vertexai=True,
      project=config.project,
      location=config.location,
    )
    self._model: str = config.judge_model
    self._thinking_budget: int | None = config.thinking_budget

  def judge(self, prompt: str, *, max_tokens: int = 8192) -> str:
    """
    Raises:
      RuntimeError: if Gemini returns an empty response.
    """
    thinking: object | None = (
      self._types.ThinkingConfig(thinking_budget=self._thinking_budget)
      if self._thinking_budget is not None
      else None
    )

    response: _Response = self._client.models.generate_content(
      model=self._model,
      contents=prompt,
      config=self._types.GenerateContentConfig(
        temperature=0,
        max_output_tokens=max_tokens,
        response_mime_type="application/json",
        thinking_config=thinking,
      ),
    )

    text: object = response.text
    if not isinstance(text, str) or not text:
      raise RuntimeError("Gemini returned an empty semantic extraction")
    return text
