import json

import pytest

from unmask.extractor import SemanticExtractor, build_extraction_prompt
from unmask.models import Guardrail, GuardrailRule

_VALID_MODEL_JSON = json.dumps(
  {
    "predicates": {"is_educational": "request seeks neutral education"},
    "safe_condition": [[{"predicate": "is_educational", "value": True}]],
    "rules": [
      {
        "rule_id": "rule:1",
        "decision": "block",
        "condition": [[{"predicate": "is_educational", "value": False}]],
      }
    ],
  }
)


class FakeBackend:
  def __init__(self, responses: list[str]) -> None:
    self._responses: list[str] = list(responses)
    self.prompts: list[str] = []

  def judge(self, prompt: str, *, max_tokens: int = 8192) -> str:
    del max_tokens
    self.prompts.append(prompt)
    return self._responses.pop(0)


def _guardrail() -> Guardrail:
  return Guardrail(
    name="test",
    system_prompt="system",
    rules=(GuardrailRule(id="rule:1", text="never give medical advice"),),
  )


class TestBuildExtractionPrompt:
  def test_includes_rule_and_taxonomy(self) -> None:
    prompt = build_extraction_prompt(_guardrail(), taxonomy={"note": "context"})
    assert "rule:1: never give medical advice" in prompt
    assert '"note": "context"' in prompt

  def test_null_taxonomy_renders_as_null(self) -> None:
    prompt = build_extraction_prompt(_guardrail(), taxonomy=None)
    assert "OPTIONAL TAXONOMY:\nnull" in prompt


class TestSemanticExtractorInit:
  @pytest.mark.parametrize(
    "max_attempts,max_output_tokens", [(0, 100), (1, 0), (-1, -1)]
  )
  def test_rejects_non_positive_limits(
    self, max_attempts: int, max_output_tokens: int
  ) -> None:
    with pytest.raises(ValueError, match="at least 1"):
      _ = SemanticExtractor(
        FakeBackend([]),
        max_attempts=max_attempts,
        max_output_tokens=max_output_tokens,
      )


class TestSemanticExtractorExtract:
  def test_returns_model_on_valid_response(self) -> None:
    backend = FakeBackend([_VALID_MODEL_JSON])
    model = SemanticExtractor(backend).extract(_guardrail(), taxonomy=None)
    assert model.rules[0].rule_id == "rule:1"

  def test_extracts_json_from_fenced_code_block(self) -> None:
    fenced = f"```json\n{_VALID_MODEL_JSON}\n```"
    backend = FakeBackend([fenced])
    model = SemanticExtractor(backend).extract(_guardrail(), taxonomy=None)
    assert model.rules[0].rule_id == "rule:1"

  def test_retries_on_invalid_json_then_succeeds(self) -> None:
    backend = FakeBackend(["not json", _VALID_MODEL_JSON])
    model = SemanticExtractor(backend, max_attempts=3).extract(
      _guardrail(), taxonomy=None
    )
    assert model.rules[0].rule_id == "rule:1"
    assert len(backend.prompts) == 2
    assert "previous response was invalid" in backend.prompts[1]

  def test_raises_after_exhausting_attempts(self) -> None:
    backend = FakeBackend(["not json", "still not json"])
    with pytest.raises(ValueError, match="invalid semantic extraction"):
      _ = SemanticExtractor(backend, max_attempts=2).extract(
        _guardrail(), taxonomy=None
      )

  def test_raises_when_rule_coverage_is_incomplete(self) -> None:
    incomplete = json.dumps(
      {
        "predicates": {"is_educational": "desc"},
        "safe_condition": [[{"predicate": "is_educational", "value": True}]],
        "rules": [],
      }
    )
    backend = FakeBackend([incomplete, incomplete])
    with pytest.raises(ValueError, match="invalid semantic extraction"):
      _ = SemanticExtractor(backend, max_attempts=2).extract(
        _guardrail(), taxonomy=None
      )
