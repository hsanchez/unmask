"""Translate a natural-language guardrail into an auditable symbolic model via an LLM judge."""

from __future__ import annotations

import json
import re
from re import Match
from typing import TYPE_CHECKING, cast

from .models import Guardrail, SymbolicModel

if TYPE_CHECKING:
  from .backend import JudgeBackend

_DEFAULT_EXTRACTION_OUTPUT_TOKENS = 32_768


def _json_object(raw: str) -> str:
  stripped: str = raw.strip()
  fenced: Match[str] | None = re.fullmatch(
    r"```(?:json)?\n(.*)\n```", stripped, re.DOTALL
  )
  if fenced:
    stripped = str(fenced.group(1))
  start: int = stripped.find("{")
  end: int = stripped.rfind("}")
  return stripped[start : end + 1] if start >= 0 and end > start else stripped


def build_extraction_prompt(guardrail: Guardrail, taxonomy: object | None) -> str:
  """Build the LLM prompt that asks a judge model to formalize `guardrail` as DNF."""
  rules: str = "\n".join(f"{rule.id}: {rule.text}" for rule in guardrail.rules)
  examples: list[dict[str, object]] = [
    {
      "content": example.content,
      "should_block": example.should_block,
      "reasoning": example.reasoning,
    }
    for example in guardrail.examples
  ]
  taxonomy_text: str = (
    json.dumps(taxonomy, indent=2) if taxonomy is not None else "null"
  )
  return f"""Translate this natural-language guardrail into an auditable boolean model.

SYSTEM PROMPT:
{guardrail.system_prompt}

RULES:
{rules}

LABELED EXAMPLES:
{json.dumps(examples, indent=2)}

OPTIONAL TAXONOMY:
{taxonomy_text}

Model conditions in disjunctive normal form: the outer list is OR, each inner
list is AND, and each literal is {{"predicate": "name", "value": true}}.

Return ONLY one JSON object with:
- predicates: mapping of snake_case boolean names to precise descriptions.
- safe_condition: DNF describing semantically legitimate/allowed requests,
  independently of whether a written rule blocks them.
- rules: one item for every rule ID, with decision ("allow" or "block"),
  condition (DNF), and rationale. A prohibition/refusal is block; an explicit
  permission/required response is allow.
- exclusions: predicate groups where members cannot both be true.
- ambiguities: unclear directions only. Flag undefined terms or thresholds,
  vague scope/references, missing exceptions, multiple plausible readings, or
  underspecified precedence. Do not call a merely broad or strict rule ambiguous.
  Each item must include two materially distinct, plausible formal
  interpretations. Each interpretation has a label, decision, condition (DNF),
  and rationale. If two distinct interpretations cannot be formalized, do not
  emit the ambiguity.

Exact shape:
{{
  "predicates": {{"is_educational": "request seeks neutral education"}},
  "safe_condition": [[{{"predicate": "is_educational", "value": true}}]],
  "rules": [{{
    "rule_id": "rule:1",
    "decision": "block",
    "condition": [[{{"predicate": "asks_for_advice", "value": true}}]],
    "rationale": "..."
  }}],
  "exclusions": [["is_educational", "asks_for_advice"]],
  "ambiguities": [{{
    "rule_id": "rule:2",
    "category": "undefined_threshold",
    "excerpt": "substantial",
    "rationale": "No threshold defines substantial.",
    "interpretation_a": {{
      "label": "any advice",
      "decision": "block",
      "condition": [[{{"predicate": "asks_for_advice", "value": true}}]],
      "rationale": "Substantial modifies the recommendation, not applicability."
    }},
    "interpretation_b": {{
      "label": "high-impact advice only",
      "decision": "allow",
      "condition": [[
        {{"predicate": "asks_for_advice", "value": true}},
        {{"predicate": "is_high_impact", "value": true}}
      ]],
      "rationale": "Substantial establishes a high-impact threshold."
    }},
    "severity": "medium"
  }}]
}}

Use shared predicates across rules whenever their meanings overlap. Preserve
exceptions as negative literals. Add predicates to distinguish ambiguity
interpretations. Do not invent policy requirements."""


class SemanticExtractor:
  """Extracts a `SymbolicModel` from a guardrail, retrying on invalid LLM output."""

  def __init__(
    self,
    backend: JudgeBackend,
    max_attempts: int = 3,
    max_output_tokens: int = _DEFAULT_EXTRACTION_OUTPUT_TOKENS,
  ) -> None:
    """
    Raises:
      ValueError: if `max_attempts` or `max_output_tokens` is less than 1.
    """
    if max_attempts < 1:
      raise ValueError("max_attempts must be at least 1")
    if max_output_tokens < 1:
      raise ValueError("max_output_tokens must be at least 1")
    self._backend: JudgeBackend = backend
    self._max_attempts: int = max_attempts
    self._max_output_tokens: int = max_output_tokens

  def extract(self, guardrail: Guardrail, taxonomy: object | None) -> SymbolicModel:
    """Ask the judge backend for a symbolic model, reprompting on invalid JSON.

    Raises:
      ValueError: if the backend still returns invalid or incomplete output
        after `max_attempts` tries.
    """
    prompt: str = build_extraction_prompt(guardrail, taxonomy)
    last_error_message: str = ""
    for _ in range(self._max_attempts):
      raw: str = self._backend.judge(
        prompt,
        max_tokens=self._max_output_tokens,
      ).text
      try:
        parsed: object = cast("object", json.loads(_json_object(raw)))
        model: SymbolicModel = SymbolicModel.from_dict(parsed)
        self._validate_rule_coverage(model, guardrail)
        return model
      except (json.JSONDecodeError, TypeError, ValueError) as exc:
        last_error_message = str(exc)
        prompt += (
          "\n\nYour previous response was invalid: "
          + f"{exc}. Return the complete corrected JSON object only."
        )
    raise ValueError(f"invalid semantic extraction: {last_error_message}")

  @staticmethod
  def _validate_rule_coverage(model: SymbolicModel, guardrail: Guardrail) -> None:
    expected: set[str] = {rule.id for rule in guardrail.rules}
    actual: set[str] = {rule.rule_id for rule in model.rules}
    if expected != actual:
      raise ValueError(
        "symbolic rules must exactly cover guardrail rules: "
        + f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
      )
