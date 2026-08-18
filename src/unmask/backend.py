"""LLM judge backends used to extract a symbolic model from a guardrail."""

from __future__ import annotations

import os
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
  from collections.abc import Callable
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
  def __call__(
    self,
    *,
    vertexai: bool | None = None,
    project: str | None = None,
    location: str | None = None,
  ) -> _Client: ...


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


class _ChatMessage(Protocol):
  content: str | None


class _ChatChoice(Protocol):
  message: _ChatMessage


class _ChatCompletion(Protocol):
  choices: list[_ChatChoice]


class _ChatCompletions(Protocol):
  def create(
    self,
    *,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: int,
  ) -> _ChatCompletion: ...


class _Chat(Protocol):
  completions: _ChatCompletions


class _OpenAIClient(Protocol):
  chat: _Chat


class _OpenAIClientFactory(Protocol):
  def __call__(self, *, api_key: str) -> _OpenAIClient: ...


class _OpenAIModule(Protocol):
  OpenAI: _OpenAIClientFactory


class _ContentBlock(Protocol):
  type: str
  text: str


class _AnthropicMessage(Protocol):
  content: list[_ContentBlock]


class _Messages(Protocol):
  def create(
    self,
    *,
    model: str,
    max_tokens: int,
    messages: list[dict[str, str]],
    temperature: int,
  ) -> _AnthropicMessage: ...


class _AnthropicClient(Protocol):
  messages: _Messages


class _AnthropicClientFactory(Protocol):
  def __call__(self, *, api_key: str) -> _AnthropicClient: ...


class _AnthropicModule(Protocol):
  Anthropic: _AnthropicClientFactory


def _load_optional_module(name: str) -> ModuleType:
  return import_module(name)


def _load_sdk_module(name: str, *, display_name: str, extra: str) -> object:
  try:
    return cast("object", _load_optional_module(name=name))
  except ImportError as exc:
    raise RuntimeError(
      f"{display_name} support is not installed; run 'uv sync --extra {extra}'"
    ) from exc


def _require_api_key(env_var: str, *, provider: str) -> str:
  api_key: str | None = os.getenv(env_var)
  if not api_key:
    raise ValueError(f"{env_var} is required for the {provider} provider")
  return api_key


class GeminiBackend:
  """Judges prompts with Gemini via the `google-genai` SDK.

  Uses Vertex AI when `config.project` (or `GOOGLE_CLOUD_PROJECT`) is set,
  and the Gemini Developer API otherwise. Credential resolution (Vertex
  application-default credentials, or `GOOGLE_API_KEY`/`GEMINI_API_KEY`) is
  delegated entirely to `google.genai.Client`.
  """

  def __init__(self, config: TargetConfig) -> None:
    """
    Raises:
      RuntimeError: if the optional `google-genai` dependency is not installed.
      ValueError: if `google.genai.Client` cannot resolve credentials for the
        selected mode (no project/location for Vertex AI, or no API key for
        the Gemini Developer API).
    """
    genai: _GenaiModule = cast(
      "_GenaiModule",
      _load_sdk_module("google.genai", display_name="Gemini", extra="gemini"),
    )
    types: _TypesModule = cast(
      "_TypesModule",
      _load_sdk_module("google.genai.types", display_name="Gemini", extra="gemini"),
    )

    self._types: _TypesModule = types
    self._client: _Client = (
      genai.Client(vertexai=True, project=config.project, location=config.location)
      if config.project
      else genai.Client()
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


class OpenAIBackend:
  """Judges prompts with OpenAI chat completions via the `openai` SDK."""

  def __init__(self, config: TargetConfig) -> None:
    """
    Raises:
      RuntimeError: if the optional `openai` dependency is not installed.
      ValueError: if `OPENAI_API_KEY` is not in the environment.
    """
    openai_module: _OpenAIModule = cast(
      "_OpenAIModule",
      _load_sdk_module("openai", display_name="OpenAI", extra="openai"),
    )
    api_key: str = _require_api_key("OPENAI_API_KEY", provider="openai")
    self._client: _OpenAIClient = openai_module.OpenAI(api_key=api_key)
    self._model: str = config.judge_model

  def judge(self, prompt: str, *, max_tokens: int = 8192) -> str:
    """
    Raises:
      RuntimeError: if OpenAI returns an empty response.
    """
    response: _ChatCompletion = self._client.chat.completions.create(
      model=self._model,
      messages=[{"role": "user", "content": prompt}],
      max_tokens=max_tokens,
      temperature=0,
    )
    text: str | None = response.choices[0].message.content if response.choices else None
    if not text:
      raise RuntimeError("OpenAI returned an empty semantic extraction")
    return text


class AnthropicBackend:
  """Judges prompts with Anthropic messages via the `anthropic` SDK."""

  def __init__(self, config: TargetConfig) -> None:
    """
    Raises:
      RuntimeError: if the optional `anthropic` dependency is not installed.
      ValueError: if `ANTHROPIC_API_KEY` is not in the environment.
    """
    anthropic_module: _AnthropicModule = cast(
      "_AnthropicModule",
      _load_sdk_module("anthropic", display_name="Anthropic", extra="anthropic"),
    )
    api_key: str = _require_api_key("ANTHROPIC_API_KEY", provider="anthropic")
    self._client: _AnthropicClient = anthropic_module.Anthropic(api_key=api_key)
    self._model: str = config.judge_model

  def judge(self, prompt: str, *, max_tokens: int = 8192) -> str:
    """
    Raises:
      RuntimeError: if Anthropic returns an empty response.
    """
    response: _AnthropicMessage = self._client.messages.create(
      model=self._model,
      max_tokens=max_tokens,
      messages=[{"role": "user", "content": prompt}],
      temperature=0,
    )
    text: str = "".join(
      block.text for block in response.content if block.type == "text"
    )
    if not text:
      raise RuntimeError("Anthropic returned an empty semantic extraction")
    return text


_PROVIDER_REGISTRY: dict[str, Callable[[TargetConfig], JudgeBackend]] = {
  "gemini": GeminiBackend,
  "openai": OpenAIBackend,
  "anthropic": AnthropicBackend,
}


def create_backend(config: TargetConfig) -> JudgeBackend:
  """Instantiate the judge backend named by `config.provider`.

  Raises:
    ValueError: if `config.provider` is not a registered provider.
    RuntimeError: if the provider's optional SDK dependency is not installed.
  """
  try:
    backend_factory: Callable[[TargetConfig], JudgeBackend] = _PROVIDER_REGISTRY[
      config.provider
    ]
  except KeyError as exc:
    available: str = ", ".join(sorted(_PROVIDER_REGISTRY))
    raise ValueError(
      f"unknown provider {config.provider!r}; available providers: {available}"
    ) from exc
  return backend_factory(config)
