import json

import pytest

from unmask.backend import JudgeResponse
from unmask.models import GuardrailExample
from unmask.repair import (
  CandidateEdit,
  FindingFingerprint,
  PolicyExampleEvaluator,
  ProposedFix,
  ProposedFixStatus,
  RepairCandidateGenerator,
  RepairGenerationError,
  TrustedPolicyExample,
  UnavailableReason,
  ValidationCheck,
  ValidationStatus,
  apply_candidate_edit,
  merge_policy_examples,
  parse_candidate_edit,
  parse_taxonomy_policy_examples,
  unified_specification_diff,
)
from unmask.service import validate_specification


class StaticBackend:
  def __init__(self, response: str) -> None:
    self.response: str = response
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
    """\
policy_examples_version: 1
policy_examples:
  - content: Neutral report
    should_block: false
    reasoning: factual
"""
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


def _natural_response(**overrides: object) -> str:
  response: dict[str, object] = {
    "target_rule_ids": ["rule:1"],
    "replacement_rules": {"rule:1": "Block advice."},
    "explanation": "Narrow the rule.",
  }
  response.update(overrides)
  return json.dumps(response)


def _parse_natural(raw: str) -> CandidateEdit:
  return parse_candidate_edit(
    raw, specification_kind="natural_language", expected_rule_ids=("rule:1",)
  )


def _parse_regex(replacement: object) -> CandidateEdit:
  return parse_candidate_edit(
    _natural_response(replacement_rules={"rule:1": replacement}),
    specification_kind="regex",
    expected_rule_ids=("rule:1",),
  )


def test_candidate_edit_accepts_a_fenced_json_response() -> None:
  candidate = _parse_natural(f"```json\n{_natural_response()}\n```")

  assert candidate.replacement_rules == {"rule:1": "Block advice."}
  assert candidate.explanation == "Narrow the rule."


@pytest.mark.parametrize(
  "raw,error,message",
  [
    ("[]", TypeError, "must be a JSON object"),
    ('{"target_rule_ids": []}', ValueError, "missing or unknown fields"),
    (_natural_response(target_rule_ids="rule:1"), TypeError, "list of strings"),
    (_natural_response(target_rule_ids=[1]), TypeError, "list of strings"),
    (_natural_response(replacement_rules=[]), TypeError, "must be a mapping"),
    (
      _natural_response(replacement_rules={"rule:2": "Other."}),
      ValueError,
      "exactly cover affected rules",
    ),
    (_natural_response(explanation="  "), ValueError, "explanation must be non-empty"),
    (_natural_response(explanation=3), ValueError, "explanation must be non-empty"),
    (
      _natural_response(replacement_rules={"rule:1": " "}),
      ValueError,
      "must be non-empty text",
    ),
    (
      _natural_response(replacement_rules={"rule:1": {"id": "rule:1"}}),
      ValueError,
      "must be non-empty text",
    ),
  ],
)
def test_natural_candidate_edit_rejects_invalid_responses(
  raw: str, error: type[Exception], message: str
) -> None:
  with pytest.raises(error, match=message):
    _ = _parse_natural(raw)


@pytest.mark.parametrize(
  "replacement,error,message",
  [
    ("Block advice.", TypeError, "must be a rule mapping"),
    (
      {"id": "rule:2", "action": "block", "pattern": "x"},
      ValueError,
      "preserve its id",
    ),
    (
      {"id": "rule:1", "action": "block", "pattern": "x", "extra": 1},
      ValueError,
      "unknown fields: extra",
    ),
  ],
)
def test_regex_candidate_edit_rejects_invalid_replacements(
  replacement: object, error: type[Exception], message: str
) -> None:
  with pytest.raises(error, match=message):
    _ = _parse_regex(replacement)


def test_regex_candidate_edit_accepts_a_rule_mapping() -> None:
  replacement: dict[str, object] = {
    "id": "rule:1",
    "action": "block",
    "pattern": "x",
    "flags": [],
  }

  assert _parse_regex(replacement).replacement_rules == {"rule:1": replacement}


@pytest.mark.parametrize(
  "text,error,message",
  [
    ("- not a mapping", TypeError, "taxonomy must be a mapping"),
    (
      "policy_examples_version: 2\npolicy_examples: []",
      ValueError,
      "version must be 1",
    ),
    (
      "policy_examples_version: 1\npolicy_examples: x",
      TypeError,
      "must be a list",
    ),
    (
      "policy_examples_version: 1\npolicy_examples: [x]",
      TypeError,
      r"\[0\] must be a mapping",
    ),
    (
      "policy_examples_version: 1\npolicy_examples:\n  - content: ' '\n    should_block: true",
      ValueError,
      "content must be non-empty",
    ),
    (
      "policy_examples_version: 1\npolicy_examples:\n  - content: a\n    should_block: yes-ish",
      TypeError,
      "should_block must be a boolean",
    ),
    (
      "policy_examples_version: 1\npolicy_examples:\n  - content: a\n    should_block: true\n    reasoning: 3",
      TypeError,
      "reasoning must be a string",
    ),
  ],
)
def test_taxonomy_policy_examples_reject_invalid_shapes(
  text: str, error: type[Exception], message: str
) -> None:
  with pytest.raises(error, match=message):
    _ = parse_taxonomy_policy_examples(text)


def test_taxonomy_without_policy_examples_yields_none() -> None:
  assert parse_taxonomy_policy_examples("other: 1\n") == ()
  assert parse_taxonomy_policy_examples("") == ()


def _evaluate(raw: str, example_count: int = 1) -> tuple[tuple[bool, str], ...]:
  examples = tuple(
    TrustedPolicyExample(f"Report {index}", False, "", "specification")
    for index in range(example_count)
  )
  return PolicyExampleEvaluator(StaticBackend(raw)).evaluate("rules: []", examples)


@pytest.mark.parametrize(
  "raw,error,message",
  [
    ('{"results": {}}', TypeError, "only a results list"),
    ('{"results": [], "extra": 1}', TypeError, "only a results list"),
    ('{"results": [1]}', TypeError, "must be mappings"),
    ('{"results": [{"index": 0}]}', ValueError, "invalid fields"),
    (
      '{"results":[{"index":0,"should_block":"no","rationale":"x"}]}',
      TypeError,
      "boolean and rationale",
    ),
    (
      '{"results":[{"index":0,"should_block":false,"rationale":7}]}',
      TypeError,
      "boolean and rationale",
    ),
    ('{"results": []}', ValueError, "cover every trusted example"),
  ],
)
def test_policy_example_evaluator_rejects_invalid_results(
  raw: str, error: type[Exception], message: str
) -> None:
  with pytest.raises(error, match=message):
    _ = _evaluate(raw)


def test_policy_example_evaluator_rejects_a_non_mapping_specification() -> None:
  backend = StaticBackend('{"results": []}')

  with pytest.raises(TypeError, match="specification must be a mapping"):
    _ = PolicyExampleEvaluator(backend).evaluate("- rule", ())


def _apply_natural(
  original: str, start: int, end: int, replacement: str = "Block advice."
) -> str:
  candidate = CandidateEdit(("rule:1",), {"rule:1": replacement}, "Reason.")
  return apply_candidate_edit(
    original,
    candidate=candidate,
    specification_kind="natural_language",
    rule_ranges={"rule:1": (start, end)},
  )


NATURAL_SOURCE = "rules:\n  - Block reports.\n"
RULE_START = NATURAL_SOURCE.index("Block reports.")
RULE_END = RULE_START + len("Block reports.")


@pytest.mark.parametrize(
  "start,end",
  [(-1, RULE_END), (RULE_START, RULE_START), (RULE_START, len(NATURAL_SOURCE) + 5)],
  ids=["negative-start", "empty-range", "range-past-end"],
)
def test_apply_candidate_rejects_invalid_ranges(start: int, end: int) -> None:
  with pytest.raises(ValueError, match="range for 'rule:1' is invalid"):
    _ = _apply_natural(NATURAL_SOURCE, start, end)


def test_apply_candidate_rejects_a_range_for_an_unknown_rule() -> None:
  candidate = CandidateEdit(("rule:2",), {"rule:2": "Block advice."}, "Reason.")

  with pytest.raises(ValueError, match="unknown rule 'rule:2'"):
    _ = apply_candidate_edit(
      NATURAL_SOURCE,
      candidate=candidate,
      specification_kind="natural_language",
      rule_ranges={"rule:1": (RULE_START, RULE_END)},
    )


def test_apply_candidate_rejects_a_natural_range_that_is_not_text() -> None:
  original = "rules:\n  - id: rule:1\n"
  start = original.index("id:")

  with pytest.raises(TypeError, match="not a text rule"):
    _ = _apply_natural(original, start, len(original))


def test_apply_candidate_rejects_a_regex_range_that_is_not_a_rule() -> None:
  candidate = CandidateEdit(
    ("rule:1",), {"rule:1": {"id": "rule:1", "action": "block", "pattern": "x"}}, "R."
  )

  with pytest.raises(TypeError, match="not a regex rule"):
    _ = apply_candidate_edit(
      "plain text\n",
      candidate=candidate,
      specification_kind="regex",
      rule_ranges={"rule:1": (0, 10)},
    )


def test_apply_candidate_rejects_overlapping_ranges() -> None:
  original = "rules:\n  - Block reports.\n  - Allow facts.\n"
  start = original.index("Block reports.")
  end = original.index("Allow facts.") + len("Allow facts.")
  candidate = CandidateEdit(
    ("rule:1", "rule:2"), {"rule:1": "A.", "rule:2": "B."}, "Reason."
  )

  with pytest.raises(ValueError, match="ranges overlap"):
    _ = apply_candidate_edit(
      original,
      candidate=candidate,
      specification_kind="natural_language",
      rule_ranges={"rule:1": (start, end), "rule:2": (start + 2, end)},
    )


def test_apply_candidate_rejects_an_unchanged_specification() -> None:
  original = 'rules:\n  - "Block reports."\n'
  start = original.index('"Block')
  end = original.index('."') + 2

  with pytest.raises(ValueError, match="does not change the specification"):
    _ = _apply_natural(original, start, end, "Block reports.")


def test_proposed_fix_serializes_optional_fields_and_check_detail() -> None:
  fix = ProposedFix(
    status=ProposedFixStatus.UNAVAILABLE,
    specification_kind="regex",
    finding_fingerprint=FindingFingerprint(
      specification_kind="regex",
      workflow="scan",
      bug_type="over_strict",
      rule_ids=("rule:1",),
      finding_id="finding",
    ),
    original_content_hash="hash",
    provider="fake",
    model="fake-model",
    validation_checks=(
      ValidationCheck("check", "Check", ValidationStatus.FAILED, "why"),
    ),
    revised_specification="revised",
    unified_diff="diff",
    explanation="explained",
    unavailable_reasons=(UnavailableReason.REGRESSION,),
  )

  payload = fix.to_dict()

  assert payload["revisedSpecification"] == "revised"
  assert payload["unifiedDiff"] == "diff"
  assert payload["explanation"] == "explained"
  assert payload["unavailableReasons"] == ["regression"]
  assert payload["validationChecks"] == [
    {"code": "check", "label": "Check", "status": "failed", "detail": "why"}
  ]
