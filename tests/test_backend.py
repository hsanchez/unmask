import pytest

import unmask.backend as backend_module
from unmask.backend import (
  AnthropicBackend,
  GeminiBackend,
  OpenAIBackend,
  create_backend,
)
from unmask.config import TargetConfig


def _target(*, provider: str = "gemini", project: str | None = None) -> TargetConfig:
  return TargetConfig(
    project=project,
    location="global",
    judge_model="unused",
    thinking_budget=None,
    provider=provider,
  )


class _Usage:
  def __init__(self, input_tokens: int, output_tokens: int) -> None:
    self.input_tokens = input_tokens
    self.output_tokens = output_tokens


def test_token_usage_normalizes_provider_counts() -> None:
  usage = backend_module._token_usage(
    _Usage(input_tokens=12, output_tokens=7),
    input_name="input_tokens",
    output_name="output_tokens",
  )

  assert usage is not None
  assert usage.to_dict() == {
    "input_tokens": 12,
    "output_tokens": 7,
    "total_tokens": 19,
  }


class TestGeminiBackendInit:
  @pytest.mark.parametrize("project", [None, "test-project"])
  def test_falls_back_when_genai_is_not_installed(self, project: str | None) -> None:
    """Without the optional `google-genai` dependency, both Vertex AI and
    Gemini Developer API modes fail the same way, before any credential
    resolution happens."""
    with pytest.raises(RuntimeError, match="Gemini support is not installed"):
      _ = GeminiBackend(_target(project=project))


class TestOpenAIBackendInit:
  def test_falls_back_when_openai_is_not_installed(self) -> None:
    with pytest.raises(RuntimeError, match="OpenAI support is not installed"):
      _ = OpenAIBackend(_target(provider="openai"))


class TestAnthropicBackendInit:
  def test_falls_back_when_anthropic_is_not_installed(self) -> None:
    with pytest.raises(RuntimeError, match="Anthropic support is not installed"):
      _ = AnthropicBackend(_target(provider="anthropic"))


class TestCreateBackend:
  @pytest.mark.parametrize(
    "provider,expected_message",
    [
      ("gemini", "Gemini support is not installed"),
      ("openai", "OpenAI support is not installed"),
      ("anthropic", "Anthropic support is not installed"),
    ],
  )
  def test_dispatches_to_the_named_provider(
    self, provider: str, expected_message: str
  ) -> None:
    with pytest.raises(RuntimeError, match=expected_message):
      _ = create_backend(_target(provider=provider))

  def test_rejects_unknown_provider(self) -> None:
    with pytest.raises(ValueError, match="unknown provider 'bogus'"):
      _ = create_backend(_target(provider="bogus"))


# --- Fake Gemini SDK, matching backend.py's _GenaiModule/_TypesModule Protocols ---


class _FakeGeminiResponse:
  def __init__(self, text: object) -> None:
    self.text: object = text


class _FakeGeminiModels:
  def __init__(self, response_text: object) -> None:
    self._response_text: object = response_text
    self.calls: list[dict[str, object]] = []

  def generate_content(
    self, *, model: str, contents: str, config: object
  ) -> _FakeGeminiResponse:
    self.calls.append({"model": model, "contents": contents, "config": config})
    return _FakeGeminiResponse(self._response_text)


class _FakeGeminiClient:
  def __init__(self, response_text: object) -> None:
    self.models: _FakeGeminiModels = _FakeGeminiModels(response_text)


class _FakeGenaiModule:
  def __init__(self, response_text: object) -> None:
    self._response_text: object = response_text
    self.client_calls: list[dict[str, object]] = []

  def Client(self, **kwargs: object) -> _FakeGeminiClient:
    self.client_calls.append(kwargs)
    return _FakeGeminiClient(self._response_text)


class _FakeTypesModule:
  def ThinkingConfig(self, **kwargs: object) -> dict[str, object]:
    return {"thinking_config": kwargs}

  def GenerateContentConfig(self, **kwargs: object) -> dict[str, object]:
    return kwargs


def _patch_loader(monkeypatch: pytest.MonkeyPatch, modules: dict[str, object]) -> None:
  def fake_loader(name: str) -> object:
    if name in modules:
      return modules[name]
    raise ImportError(name)

  monkeypatch.setattr(backend_module, "_load_optional_module", fake_loader)


def _patch_gemini_sdk(
  monkeypatch: pytest.MonkeyPatch, response_text: object
) -> _FakeGenaiModule:
  genai_module = _FakeGenaiModule(response_text)
  _patch_loader(
    monkeypatch,
    {"google.genai": genai_module, "google.genai.types": _FakeTypesModule()},
  )
  return genai_module


class TestGeminiBackendJudge:
  def test_returns_text_from_a_successful_response(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    genai_module = _patch_gemini_sdk(monkeypatch, response_text="hello world")
    backend = GeminiBackend(_target(project="test-project"))

    result = backend.judge("prompt text", max_tokens=123)

    assert result.text == "hello world"
    call = genai_module.client_calls[0]
    assert call == {
      "vertexai": True,
      "project": "test-project",
      "location": "global",
      "http_options": {"timeout": 60_000, "retry_options": {"attempts": 1}},
    }

  def test_uses_developer_api_mode_when_project_is_unset(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    genai_module = _patch_gemini_sdk(monkeypatch, response_text="ok")
    _ = GeminiBackend(_target(project=None))

    assert genai_module.client_calls == [
      {"http_options": {"timeout": 60_000, "retry_options": {"attempts": 1}}}
    ]

  @pytest.mark.parametrize("response_text", ["", None])
  def test_raises_on_empty_response(
    self, monkeypatch: pytest.MonkeyPatch, response_text: object
  ) -> None:
    _ = _patch_gemini_sdk(monkeypatch, response_text=response_text)
    backend = GeminiBackend(_target(project="test-project"))

    with pytest.raises(RuntimeError, match="Gemini returned an empty"):
      _ = backend.judge("prompt text")


# --- Fake OpenAI SDK, matching backend.py's _OpenAIModule Protocol ---


class _FakeChatMessage:
  def __init__(self, content: str | None) -> None:
    self.content: str | None = content


class _FakeChatChoice:
  def __init__(self, content: str | None) -> None:
    self.message: _FakeChatMessage = _FakeChatMessage(content)


class _FakeChatCompletion:
  def __init__(self, choices: list[_FakeChatChoice]) -> None:
    self.choices: list[_FakeChatChoice] = choices


class _FakeChatCompletions:
  def __init__(self, choices: list[_FakeChatChoice]) -> None:
    self._choices: list[_FakeChatChoice] = choices
    self.calls: list[dict[str, object]] = []

  def create(self, **kwargs: object) -> _FakeChatCompletion:
    self.calls.append(kwargs)
    return _FakeChatCompletion(self._choices)


class _FakeChat:
  def __init__(self, choices: list[_FakeChatChoice]) -> None:
    self.completions: _FakeChatCompletions = _FakeChatCompletions(choices)


class _FakeOpenAIClient:
  def __init__(self, choices: list[_FakeChatChoice]) -> None:
    self.chat: _FakeChat = _FakeChat(choices)


class _FakeOpenAIModule:
  def __init__(self, choices: list[_FakeChatChoice]) -> None:
    self._choices: list[_FakeChatChoice] = choices
    self.client_calls: list[dict[str, object]] = []
    self.created_clients: list[_FakeOpenAIClient] = []

  def OpenAI(self, **kwargs: object) -> _FakeOpenAIClient:
    self.client_calls.append(kwargs)
    client = _FakeOpenAIClient(self._choices)
    self.created_clients.append(client)
    return client


def _patch_openai_sdk(
  monkeypatch: pytest.MonkeyPatch, choices: list[_FakeChatChoice]
) -> _FakeOpenAIModule:
  openai_module = _FakeOpenAIModule(choices)
  _patch_loader(monkeypatch, {"openai": openai_module})
  return openai_module


class TestOpenAIBackendJudge:
  def test_returns_text_and_constructs_client_with_the_api_key(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    openai_module = _patch_openai_sdk(
      monkeypatch, choices=[_FakeChatChoice("hello world")]
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    backend = OpenAIBackend(_target(provider="openai"))

    result = backend.judge("prompt text", max_tokens=123)

    assert result.text == "hello world"
    assert openai_module.client_calls == [
      {"api_key": "test-key", "timeout": 60, "max_retries": 0}
    ]

  def test_request_carries_prompt_model_and_max_tokens(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    openai_module = _patch_openai_sdk(monkeypatch, choices=[_FakeChatChoice("ok")])
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    backend = OpenAIBackend(_target(provider="openai"))

    _ = backend.judge("prompt text", max_tokens=123)

    call = openai_module.created_clients[0].chat.completions.calls[0]
    assert call["model"] == "unused"
    assert call["messages"] == [{"role": "user", "content": "prompt text"}]
    assert call["max_tokens"] == 123
    assert call["temperature"] == 0

  def test_raises_when_choices_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: an empty `choices` list must not leak an IndexError."""
    _ = _patch_openai_sdk(monkeypatch, choices=[])
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    backend = OpenAIBackend(_target(provider="openai"))

    with pytest.raises(RuntimeError, match="OpenAI returned an empty"):
      _ = backend.judge("prompt text")

  def test_raises_when_message_content_is_none(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    _ = _patch_openai_sdk(monkeypatch, choices=[_FakeChatChoice(None)])
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    backend = OpenAIBackend(_target(provider="openai"))

    with pytest.raises(RuntimeError, match="OpenAI returned an empty"):
      _ = backend.judge("prompt text")

  def test_requires_api_key_even_when_sdk_is_installed(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    _ = _patch_openai_sdk(monkeypatch, choices=[])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
      _ = OpenAIBackend(_target(provider="openai"))


# --- Fake Anthropic SDK, matching backend.py's _AnthropicModule Protocol ---


class _FakeContentBlock:
  def __init__(self, block_type: str, text: str = "") -> None:
    self.type: str = block_type
    self.text: str = text


class _FakeAnthropicMessage:
  def __init__(self, content: list[_FakeContentBlock]) -> None:
    self.content: list[_FakeContentBlock] = content


class _FakeMessages:
  def __init__(self, content: list[_FakeContentBlock]) -> None:
    self._content: list[_FakeContentBlock] = content
    self.calls: list[dict[str, object]] = []

  def create(self, **kwargs: object) -> _FakeAnthropicMessage:
    self.calls.append(kwargs)
    return _FakeAnthropicMessage(self._content)


class _FakeAnthropicClient:
  def __init__(self, content: list[_FakeContentBlock]) -> None:
    self.messages: _FakeMessages = _FakeMessages(content)


class _FakeAnthropicModule:
  def __init__(self, content: list[_FakeContentBlock]) -> None:
    self._content: list[_FakeContentBlock] = content
    self.client_calls: list[dict[str, object]] = []

  def Anthropic(self, **kwargs: object) -> _FakeAnthropicClient:
    self.client_calls.append(kwargs)
    return _FakeAnthropicClient(self._content)


def _patch_anthropic_sdk(
  monkeypatch: pytest.MonkeyPatch, content: list[_FakeContentBlock]
) -> _FakeAnthropicModule:
  anthropic_module = _FakeAnthropicModule(content)
  _patch_loader(monkeypatch, {"anthropic": anthropic_module})
  return anthropic_module


class TestAnthropicBackendJudge:
  def test_returns_text_from_a_single_text_block(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    anthropic_module = _patch_anthropic_sdk(
      monkeypatch, content=[_FakeContentBlock("text", text="hello world")]
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    backend = AnthropicBackend(_target(provider="anthropic"))

    result = backend.judge("prompt text", max_tokens=123)

    assert result.text == "hello world"
    assert anthropic_module.client_calls == [
      {"api_key": "test-key", "timeout": 60, "max_retries": 0}
    ]

  def test_concatenates_only_text_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: non-text blocks (e.g. extended-thinking output) must
    be excluded from the extracted text, not just the first block used."""
    _ = _patch_anthropic_sdk(
      monkeypatch,
      content=[
        _FakeContentBlock("thinking", text="reasoning about the answer"),
        _FakeContentBlock("text", text="hello "),
        _FakeContentBlock("text", text="world"),
      ],
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    backend = AnthropicBackend(_target(provider="anthropic"))

    result = backend.judge("prompt text")

    assert result.text == "hello world"

  def test_raises_when_content_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _patch_anthropic_sdk(monkeypatch, content=[])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    backend = AnthropicBackend(_target(provider="anthropic"))

    with pytest.raises(RuntimeError, match="Anthropic returned an empty"):
      _ = backend.judge("prompt text")

  def test_raises_when_only_non_text_blocks_are_present(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    _ = _patch_anthropic_sdk(
      monkeypatch, content=[_FakeContentBlock("thinking", text="reasoning")]
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    backend = AnthropicBackend(_target(provider="anthropic"))

    with pytest.raises(RuntimeError, match="Anthropic returned an empty"):
      _ = backend.judge("prompt text")

  def test_requires_api_key_even_when_sdk_is_installed(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    _ = _patch_anthropic_sdk(monkeypatch, content=[])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
      _ = AnthropicBackend(_target(provider="anthropic"))
