import json

import pytest

from unmask.backend import JudgeResponse
from unmask.models import GuardrailExample
from unmask.repair import (
  CandidateEdit,
  PolicyExampleEvaluator,
  RepairCandidateGenerator,
  RepairGenerationError,
  TrustedPolicyExample,
  apply_candidate_edit,
  merge_policy_examples,
  parse_taxonomy_policy_examples,
  unified_specification_diff,
)
from unmask.service import validate_specification


class StaticBackend:
  def __init__(self, response: str) -> None:
    self.response = response
    self.prompts: list[str] = []

  def judge(self, prompt: str, *, max_tokens: int = 8192) -> JudgeResponse:
    del max_tokens
    self.prompts.append(prompt)
    return JudgeResponse(self.response)


def test_candidate_generator_rejects_changes_to_unaffected_rules() -> None:
  backend = StaticBackend(
    json.dumps(
      {
        "target_rule_ids": ["rule:1", "rule:2"],
        "replacement_rules": {"rule:1": "Allow reports.", "rule:2": "Other."},
        "explanation": "Narrow the rule.",
      }
    )
  )

  with pytest.raises(RepairGenerationError, match="exactly the affected rules"):
    _ = RepairCandidateGenerator(backend).generate(
      specification_kind="natural_language",
      finding={},
      affected_rules={"rule:1": "Block reports."},
      examples=[],
      expected_rule_ids=("rule:1",),
    )


def test_apply_natural_candidate_preserves_unaffected_source() -> None:
  original = (
    "name: sample\nsystem_prompt: help\nrules:\n  - Block reports.\n  - Allow facts.\n"
  )
  start = original.index("Block reports.")
  candidate = CandidateEdit(
    target_rule_ids=("rule:1",),
    replacement_rules={"rule:1": "Block investment recommendations."},
    explanation="Narrowed scope.",
  )

  revised = apply_candidate_edit(
    original,
    candidate=candidate,
    specification_kind="natural_language",
    rule_ranges={"rule:1": (start, start + len("Block reports."))},
  )

  assert '  - "Block investment recommendations."' in revised
  assert "  - Allow facts." in revised


def test_apply_natural_candidate_replaces_block_scalar_without_joining_next_rule() -> (
  None
):
  original = (
    "name: sample\nsystem_prompt: help\nrules:\n  - |\n"
    "    Block reports.\n    Explain why.\n  - Allow facts.\n"
  )
  start = original.index("|")
  end = original.index("  - Allow facts.")
  candidate = CandidateEdit(
    target_rule_ids=("rule:1",),
    replacement_rules={"rule:1": "Block investment recommendations."},
    explanation="Narrowed scope.",
  )

  revised = apply_candidate_edit(
    original,
    candidate=candidate,
    specification_kind="natural_language",
    rule_ranges={"rule:1": (start, end)},
  )

  assert '  - "Block investment recommendations."\n  - Allow facts.' in revised


def test_apply_regex_candidate_preserves_sequence_indentation() -> None:
  original = (
    "kind: regex\nname: sample\nrules:\n"
    "  - id: block-invest\n    action: block\n    pattern: invest\n"
  )
  start = original.index("id: block-invest")
  end = len(original.rstrip("\n"))
  candidate = CandidateEdit(
    target_rule_ids=("block-invest",),
    replacement_rules={
      "block-invest": {
        "id": "block-invest",
        "action": "block",
        "pattern": "recommend.*invest",
      }
    },
    explanation="Narrowed scope.",
  )

  revised = apply_candidate_edit(
    original,
    candidate=candidate,
    specification_kind="regex",
    rule_ranges={"block-invest": (start, end)},
  )

  assert (
    "  - id: block-invest\n    action: block\n    pattern: recommend.*invest" in revised
  )


def test_apply_candidate_uses_parser_ranges_with_crlf_and_unicode() -> None:
  original = (
    "schema_version: 1\r\nkind: regex\r\nname: café\r\nengine: python_re\r\n"
    "scope: agent_output\r\nrules:\r\n"
    "  - id: block-café\r\n    action: block\r\n    pattern: café\r\n"
  )
  validation = validate_specification(original)
  unit = validation.document.structural_units[0]
  candidate = CandidateEdit(
    target_rule_ids=("block-café",),
    replacement_rules={
      "block-café": {
        "id": "block-café",
        "action": "block",
        "pattern": "café recommendation",
      }
    },
    explanation="Narrowed scope.",
  )

  revised = apply_candidate_edit(
    original,
    candidate=candidate,
    specification_kind="regex",
    rule_ranges={"block-café": (unit.start, unit.end)},
  )

  assert "name: café\r\n" in revised
  assert "pattern: café recommendation" in revised
  assert validate_specification(revised).name == "café"


def test_apply_candidate_rejects_range_for_another_rule() -> None:
  original = "id: other\naction: block\npattern: hack\n"
  candidate = CandidateEdit(
    target_rule_ids=("expected",),
    replacement_rules={
      "expected": {"id": "expected", "action": "block", "pattern": "safe"}
    },
    explanation="Replacement.",
  )

  with pytest.raises(ValueError, match="points at another rule"):
    _ = apply_candidate_edit(
      original,
      candidate=candidate,
      specification_kind="regex",
      rule_ranges={"expected": (0, len(original))},
    )


def test_unified_diff_preserves_unicode_and_missing_final_newline() -> None:
  patch = unified_specification_diff(
    "rule: café", "rule: café safe", filename="guardrail.yaml"
  )

  assert "--- guardrail.yaml" in patch
  assert "-rule: café" in patch
  assert "+rule: café safe" in patch
  assert patch.count("\\ No newline at end of file") == 2


def test_unified_diff_preserves_crlf_content_lines() -> None:
  patch = unified_specification_diff(
    "rule: old\r\n", "rule: new\r\n", filename="guardrail.yaml"
  )

  assert "-rule: old\r\n" in patch
  assert "+rule: new\r\n" in patch


def test_unified_diff_keeps_unicode_separators_inside_lines() -> None:
  patch = unified_specification_diff(
    "rule: old\u2028word\n", "rule: new\u2028word\n", filename="guardrail.yaml"
  )

  assert "-rule: old\u2028word\n" in patch
  assert "+rule: new\u2028word\n" in patch
  assert "No newline" not in patch


def test_policy_taxonomy_examples_merge_and_reject_conflicts() -> None:
  taxonomy = parse_taxonomy_policy_examples(
    "policy_examples_version: 1\npolicy_examples:\n"
    "  - content: Neutral report\n    should_block: false\n    reasoning: factual\n"
  )
  merged = merge_policy_examples(
    (GuardrailExample(content="Advice", should_block=True),), taxonomy
  )

  assert len(merged) == 2
  with pytest.raises(ValueError, match="conflicting expected outcomes"):
    _ = merge_policy_examples(
      (GuardrailExample(content="Neutral report", should_block=True),), taxonomy
    )


def test_policy_examples_reject_inline_conflicts_and_deduplicate_labels() -> None:
  with pytest.raises(ValueError, match="conflicting expected outcomes"):
    _ = merge_policy_examples(
      (
        GuardrailExample(content="Same case", should_block=True),
        GuardrailExample(content="Same case", should_block=False),
      ),
      (),
    )

  merged = merge_policy_examples(
    (
      GuardrailExample(content="Same case", should_block=True),
      GuardrailExample(content="Same case", should_block=True),
    ),
    (),
  )
  assert len(merged) == 1


def test_policy_example_evaluator_requires_complete_ordered_results() -> None:
  backend = StaticBackend(
    '{"results":[{"index":0,"should_block":false,"rationale":"Allowed."}]}'
  )
  examples = (
    TrustedPolicyExample(
      content="Neutral report",
      should_block=False,
      reasoning="factual",
      source="specification",
    ),
  )

  assert PolicyExampleEvaluator(backend).evaluate("rules: []", examples) == (
    (False, "Allowed."),
  )


def test_policy_example_evaluator_hides_expected_labels() -> None:
  backend = StaticBackend(
    '{"results":[{"index":0,"should_block":false,"rationale":"Allowed."}]}'
  )
  examples = (
    TrustedPolicyExample("Neutral report", False, "SECRET_REASON", "specification"),
  )
  specification = (
    "rules:\n  - Allow neutral reports.\nexamples:\n"
    "  - content: Neutral report\n    should_block: false\n"
    "    reasoning: SECRET_REASON\n"
  )

  _ = PolicyExampleEvaluator(backend).evaluate(specification, examples)

  prompt = backend.prompts[0]
  assert "SECRET_REASON" not in prompt
  assert "examples:" not in prompt
  assert "Allow neutral reports." in prompt


def test_policy_example_evaluator_rejects_boolean_index() -> None:
  backend = StaticBackend(
    '{"results":[{"index":false,"should_block":false,"rationale":"Allowed."}]}'
  )
  examples = (TrustedPolicyExample("Report", False, "", "specification"),)

  with pytest.raises(ValueError, match="complete and ordered"):
    _ = PolicyExampleEvaluator(backend).evaluate("rules: []", examples)
