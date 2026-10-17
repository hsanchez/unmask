import json
from typing import cast

import pytest
import yaml

from tests.helpers import (
  EXAMPLES,
  ConflictingRulesBackend,
  json_list,
  json_object,
  load_json_object,
  target_config,
)
from unmask.backend import JudgeBackend, JudgeResponse
from unmask.diagnosis import RegexManifest
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
  propose_fix,
  regex_examples_retained,
  unified_specification_diff,
)
from unmask.scanning import RegexScanReport
from unmask.service import (
  bugs,
  diagnose_specification,
  validate_specification,
)


class StaticBackend:
  def __init__(self, response: str) -> None:
    self.response: str = response
    self.prompts: list[str] = []

  def judge(self, prompt: str, *, max_tokens: int = 8192) -> JudgeResponse:
    del max_tokens
    self.prompts.append(prompt)
    return JudgeResponse(self.response)


class SequenceBackend:
  def __init__(self, responses: list[str]) -> None:
    self.responses: list[str] = responses

  def judge(self, prompt: str, *, max_tokens: int = 8192) -> JudgeResponse:
    del prompt, max_tokens
    if not self.responses:
      raise AssertionError("unexpected backend call")
    return JudgeResponse(self.responses.pop(0))


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


def test_empty_regex_example_set_is_vacuously_retained() -> None:
  manifest = RegexManifest.from_text(
    """\
schema_version: 1
kind: regex
name: sample
engine: python_re
scope: agent_output
rules:
  - id: block-hack
    action: block
    pattern: hack
"""
  )
  report = RegexScanReport(manifest=manifest, cases=(), taxonomy_supplied=False)

  assert regex_examples_retained(report, report, target_example_hash=None) == (True, 0)


def test_regex_proposed_fix_is_validated_before_it_is_returned() -> None:
  text = (
    "schema_version: 1\nkind: regex\nname: sample\nengine: python_re\n"
    "scope: agent_output\nrules:\n"
    "  - id: block-hack\n    action: block\n    pattern: hack\n"
    "examples:\n  - content: harmless hack report\n"
    "    expected_action: allow\n    reason: neutral reporting\n"
    "  - content: malicious hack\n"
    "    expected_action: block\n    reason: unsafe request\n"
  )
  baseline = bugs(text, model=target_config())
  finding = next(item for item in baseline.findings if "block-hack" in item.rule_ids)
  response = json.dumps(
    {
      "target_rule_ids": list(finding.rule_ids),
      "replacement_rules": {
        rule_id: (
          {
            "id": "block-hack",
            "action": "block",
            "pattern": "^malicious hack$",
          }
          if rule_id == "block-hack"
          else {}
        )
        for rule_id in finding.rule_ids
      },
      "explanation": "Restrict blocking to the unsafe phrase.",
    }
  )
  backend = SequenceBackend([response])

  result = propose_fix(
    text,
    specification_name="sample.yaml",
    taxonomy_text=None,
    violations_text=None,
    workflow="scan",
    finding_id=finding.id,
    expected_content_hash=baseline.content_hash,
    target=target_config(),
    backend_factory=lambda config: backend,
  )

  assert result.status == "validated", result.to_dict()
  assert result.unified_diff is not None
  assert "+    pattern: ^malicious hack$" in result.unified_diff
  assert all(check.status == "passed" for check in result.validation_checks)


def test_regex_diagnosis_fix_uses_labeled_incidents_as_regression_cases() -> None:
  text = (
    "schema_version: 1\nkind: regex\nname: sample\nengine: python_re\n"
    "scope: agent_output\nrules:\n"
    "  - id: block-hack\n    action: block\n    pattern: hack\n"
  )
  violations = json.dumps(
    {
      "violations": [
        {
          "context": "harmless hack report",
          "location": 9,
          "value": "hack",
          "expected_outcome": "allow",
          "expected_reason": "Neutral reporting.",
        }
      ]
    }
  )
  baseline = diagnose_specification(text, violations)
  candidate_response = json.dumps(
    {
      "target_rule_ids": ["block-hack"],
      "replacement_rules": {
        "block-hack": {
          "id": "block-hack",
          "action": "block",
          "pattern": "^malicious hack$",
        }
      },
      "explanation": "Restrict blocking to the unsafe phrase.",
    }
  )
  backend = SequenceBackend([candidate_response])

  result = propose_fix(
    text,
    specification_name="sample.yaml",
    taxonomy_text=None,
    violations_text=violations,
    workflow="diagnose",
    finding_id=baseline.findings[0].id,
    expected_content_hash=baseline.content_hash,
    target=target_config(),
    backend_factory=lambda config: backend,
  )

  assert result.status == "validated", result.to_dict()
  examples_check = next(
    check for check in result.validation_checks if check.code == "examples_retained"
  )
  assert examples_check.label == "1 trusted cases retained"


def test_regex_diagnosis_fix_rejects_new_scan_findings() -> None:
  text = (
    "schema_version: 1\nkind: regex\nname: sample\nengine: python_re\n"
    "scope: agent_output\nrules:\n"
    "  - id: block-hack\n    action: block\n    pattern: hack\n"
    "examples:\n  - content: safe report\n"
    "    expected_action: allow\n    reason: benign\n"
  )
  violations = json.dumps(
    {
      "violations": [
        {
          "context": "harmless hack report",
          "location": 9,
          "value": "hack",
          "expected_outcome": "allow",
          "expected_reason": "Neutral reporting.",
        }
      ]
    }
  )
  baseline = diagnose_specification(text, violations)
  backend = SequenceBackend(
    [
      json.dumps(
        {
          "target_rule_ids": ["block-hack"],
          "replacement_rules": {
            "block-hack": {
              "id": "block-hack",
              "action": "block",
              "pattern": "safe",
            }
          },
          "explanation": "Move the match away from the incident.",
        }
      )
    ]
  )

  result = propose_fix(
    text,
    specification_name="sample.yaml",
    taxonomy_text=None,
    violations_text=violations,
    workflow="diagnose",
    finding_id=baseline.findings[0].id,
    expected_content_hash=baseline.content_hash,
    target=target_config(),
    backend_factory=lambda config: backend,
  )

  assert result.status == "unavailable"
  no_new = next(
    check for check in result.validation_checks if check.code == "no_new_findings"
  )
  assert no_new.status == "failed"
  assert "new_findings" in result.unavailable_reasons


def test_regex_diagnosis_retains_duplicate_content_cases_by_position() -> None:
  text = (
    "schema_version: 1\nkind: regex\nname: sample\nengine: python_re\n"
    "scope: agent_output\nrules:\n"
    "  - id: block-hack\n    action: block\n    pattern: hack\n"
  )
  violations = json.dumps(
    {
      "violations": [
        {
          "context": "hack",
          "location": 0,
          "value": "hack",
          "expected_outcome": "block",
          "expected_reason": "Unsafe request.",
        },
        {
          "context": "hack",
          "location": 0,
          "value": "hack",
          "expected_outcome": "allow",
          "expected_reason": "Quoted example.",
        },
      ]
    }
  )
  baseline = diagnose_specification(text, violations)
  target_finding = baseline.findings[0]
  backend = SequenceBackend(
    [
      json.dumps(
        {
          "target_rule_ids": ["block-hack"],
          "replacement_rules": {
            "block-hack": {
              "id": "block-hack",
              "action": "block",
              "pattern": "^$",
            }
          },
          "explanation": "Stop matching the quoted example.",
        }
      )
    ]
  )

  result = propose_fix(
    text,
    specification_name="sample.yaml",
    taxonomy_text=None,
    violations_text=violations,
    workflow="diagnose",
    finding_id=target_finding.id,
    expected_content_hash=baseline.content_hash,
    target=target_config(),
    backend_factory=lambda config: backend,
  )

  examples_check = next(
    check for check in result.validation_checks if check.code == "examples_retained"
  )
  assert result.status == "unavailable"
  assert examples_check.status == "failed"


def test_unvalidated_regex_candidate_text_is_not_returned() -> None:
  text = (
    "schema_version: 1\nkind: regex\nname: sample\nengine: python_re\n"
    "scope: agent_output\nrules:\n"
    "  - id: block-hack\n    action: block\n    pattern: hack\n"
    "examples:\n  - content: harmless hack report\n"
    "    expected_action: allow\n    reason: neutral reporting\n"
  )
  baseline = bugs(text, model=target_config())
  unsafe_pattern = "hack|private-candidate-text"
  backend = SequenceBackend(
    [
      json.dumps(
        {
          "target_rule_ids": ["block-hack"],
          "replacement_rules": {
            "block-hack": {
              "id": "block-hack",
              "action": "block",
              "pattern": unsafe_pattern,
            }
          },
          "explanation": "This does not repair the finding.",
        }
      )
    ]
  )

  result = propose_fix(
    text,
    specification_name="sample.yaml",
    taxonomy_text=None,
    violations_text=None,
    workflow="scan",
    finding_id=baseline.findings[0].id,
    expected_content_hash=baseline.content_hash,
    target=target_config(),
    backend_factory=lambda config: backend,
  )

  assert result.status == "unavailable"
  assert result.revised_specification is None
  assert result.unified_diff is None
  assert unsafe_pattern not in json.dumps(result.to_dict())
  assert "target_finding_remains" in result.unavailable_reasons


def test_regex_scan_retains_duplicate_content_cases_by_position() -> None:
  text = (
    "schema_version: 1\nkind: regex\nname: sample\nengine: python_re\n"
    "scope: agent_output\nrules:\n"
    "  - id: block-hack\n    action: block\n    pattern: hack\n"
    "examples:\n"
    "  - content: malicious hack\n    expected_action: block\n    reason: Unsafe.\n"
    "  - content: malicious hack\n    expected_action: block\n    reason: Unsafe.\n"
    "  - content: harmless hack report\n"
    "    expected_action: allow\n    reason: Neutral reporting.\n"
  )
  baseline = bugs(text, model=target_config())
  backend = SequenceBackend(
    [
      json.dumps(
        {
          "target_rule_ids": ["block-hack"],
          "replacement_rules": {
            "block-hack": {
              "id": "block-hack",
              "action": "block",
              "pattern": "^malicious hack$",
            }
          },
          "explanation": "Restrict blocking to the unsafe phrase.",
        }
      )
    ]
  )

  result = propose_fix(
    text,
    specification_name="sample.yaml",
    taxonomy_text=None,
    violations_text=None,
    workflow="scan",
    finding_id=baseline.findings[0].id,
    expected_content_hash=baseline.content_hash,
    target=target_config(),
    backend_factory=lambda config: backend,
  )

  examples_check = next(
    check for check in result.validation_checks if check.code == "examples_retained"
  )
  assert result.status == "validated", result.to_dict()
  assert examples_check.status == "passed"
  assert examples_check.label == "3 trusted cases retained"


@pytest.mark.parametrize("bug_type", ["over_strict", "contradiction", "ambiguity"])
def test_regex_proposed_fix_supports_each_finding_class(bug_type: str) -> None:
  text = (EXAMPLES / "regexes.yaml").read_text(encoding="utf-8")
  baseline = bugs(text, model=target_config())
  finding = next(item for item in baseline.findings if item.bug_type == bug_type)
  manifest = json_object(cast("object", yaml.safe_load(text)))
  manifest_rules = {
    json_object(rule)["id"]: json_object(rule) for rule in json_list(manifest["rules"])
  }
  replacements = {
    rule_id: dict(manifest_rules[rule_id]) for rule_id in finding.rule_ids
  }
  if bug_type == "over_strict":
    replacements["block-investment-term"]["pattern"] = r"\AYou should invest\b"
  elif bug_type == "contradiction":
    replacements["allow-how-to-education"]["pattern"] = r"(?<=how to )trade\b(?! Acme)"
  else:
    interpretations = json_list(
      replacements["first-time-investor-scope"]["interpretations"]
    )
    json_object(interpretations[1])["pattern"] = json_object(interpretations[0])[
      "pattern"
    ]
  backend = SequenceBackend(
    [
      json.dumps(
        {
          "target_rule_ids": list(finding.rule_ids),
          "replacement_rules": replacements,
          "explanation": f"Repair the {bug_type} finding.",
        }
      )
    ]
  )

  result = propose_fix(
    text,
    specification_name="regexes.yaml",
    taxonomy_text=None,
    violations_text=None,
    workflow="scan",
    finding_id=finding.id,
    expected_content_hash=baseline.content_hash,
    target=target_config(),
    backend_factory=lambda config: backend,
  )

  assert result.status == "validated", result.to_dict()


def test_natural_proposed_fix_requires_trusted_examples() -> None:
  text = (
    "name: sample\nsystem_prompt: Be helpful.\nrules:\n"
    "  - Block all advice.\n  - Allow safe advice.\n"
  )
  baseline = bugs(
    text,
    model=target_config(),
    backend_factory=lambda config: ConflictingRulesBackend(),
  )
  backend = ConflictingRulesBackend()

  result = propose_fix(
    text,
    specification_name="sample.yaml",
    taxonomy_text=None,
    violations_text=None,
    workflow="scan",
    finding_id=baseline.findings[0].id,
    expected_content_hash=baseline.content_hash,
    target=target_config(),
    backend_factory=lambda config: backend,
  )

  assert result.status == "unavailable"
  assert result.unavailable_reasons == ("insufficient_evidence",)


def _symbolic_response(*, repaired: bool) -> str:
  condition_value = True
  safe_value = False if repaired else True
  return json.dumps(
    {
      "predicates": {"is_recommendation": "content recommends an investment"},
      "safe_condition": [[{"predicate": "is_recommendation", "value": safe_value}]],
      "rules": [
        {
          "rule_id": "rule:1",
          "decision": "block",
          "condition": [[{"predicate": "is_recommendation", "value": condition_value}]],
          "rationale": "Block recommendations.",
        }
      ],
      "exclusions": [],
      "ambiguities": [],
    }
  )


def test_natural_proposed_fix_requires_stable_extraction_and_examples() -> None:
  text = (
    "name: sample\nsystem_prompt: Be helpful.\nrules:\n"
    "  - Block any mention of investing.\n"
    "examples:\n"
    "  - content: Anthropic will invest.\n    should_block: false\n"
    "    reasoning: Neutral report.\n"
    "  - content: You should invest.\n    should_block: true\n"
    "    reasoning: Recommendation.\n"
  )
  target = target_config()
  baseline_backend = SequenceBackend([_symbolic_response(repaired=False)])
  baseline = bugs(text, model=target, backend_factory=lambda config: baseline_backend)
  candidate_response = json.dumps(
    {
      "target_rule_ids": ["rule:1"],
      "replacement_rules": {
        "rule:1": (
          "Block recommendations that the user make a specific investment. "
          "Allow neutral reporting about third-party investments."
        )
      },
      "explanation": "Distinguish recommendations from neutral reporting.",
    }
  )
  evaluation_response = json.dumps(
    {
      "results": [
        {"index": 0, "should_block": False, "rationale": "Neutral report."},
        {"index": 1, "should_block": True, "rationale": "Recommendation."},
      ]
    }
  )
  backend = SequenceBackend(
    [
      _symbolic_response(repaired=False),
      candidate_response,
      _symbolic_response(repaired=True),
      _symbolic_response(repaired=True),
      evaluation_response,
      evaluation_response,
    ]
  )

  result = propose_fix(
    text,
    specification_name="sample.yaml",
    taxonomy_text=None,
    violations_text=None,
    workflow="scan",
    finding_id=baseline.findings[0].id,
    expected_content_hash=baseline.content_hash,
    target=target,
    backend_factory=lambda config: backend,
  )

  assert result.status == "validated", result.to_dict()
  assert result.unified_diff is not None
  assert "Allow neutral reporting" in result.unified_diff
  assert {check.code for check in result.validation_checks} >= {
    "stable_extraction",
    "examples_retained",
    "target_removed",
    "no_new_findings",
  }


def test_natural_fix_rejects_changed_predicate_meaning_as_unstable() -> None:
  text = (
    "name: sample\nsystem_prompt: Be helpful.\nrules:\n"
    "  - Block any mention of investing.\n"
    "examples:\n"
    "  - content: Neutral report.\n    should_block: false\n"
    "    reasoning: Neutral.\n"
  )
  target = target_config()
  baseline_backend = SequenceBackend([_symbolic_response(repaired=False)])
  baseline = bugs(text, model=target, backend_factory=lambda config: baseline_backend)
  candidate_response = json.dumps(
    {
      "target_rule_ids": ["rule:1"],
      "replacement_rules": {"rule:1": "Block investment recommendations."},
      "explanation": "Narrow the rule.",
    }
  )
  changed_model = load_json_object(_symbolic_response(repaired=True))
  json_object(changed_model["predicates"])["is_recommendation"] = (
    "content mentions an investment"
  )
  evaluation_response = json.dumps(
    {"results": [{"index": 0, "should_block": False, "rationale": "Neutral."}]}
  )
  backend = SequenceBackend(
    [
      _symbolic_response(repaired=False),
      candidate_response,
      _symbolic_response(repaired=True),
      json.dumps(changed_model),
      evaluation_response,
      evaluation_response,
    ]
  )

  result = propose_fix(
    text,
    specification_name="sample.yaml",
    taxonomy_text=None,
    violations_text=None,
    workflow="scan",
    finding_id=baseline.findings[0].id,
    expected_content_hash=baseline.content_hash,
    target=target,
    backend_factory=lambda config: backend,
  )

  stability_check = next(
    check for check in result.validation_checks if check.code == "stable_extraction"
  )
  assert result.status == "unavailable"
  assert stability_check.status == "failed"
  assert "unstable_extraction" in result.unavailable_reasons


def _natural_finding_model(bug_type: str, *, repaired: bool) -> str:
  rules = [
    {
      "rule_id": "rule:1",
      "decision": "block",
      "condition": [
        [
          {
            "predicate": "is_recommendation",
            "value": bug_type != "over_strict" or repaired,
          }
        ]
      ],
      "rationale": "Block recommendations.",
    }
  ]
  if bug_type == "contradiction":
    rules.append(
      {
        "rule_id": "rule:2",
        "decision": "allow",
        "condition": [[{"predicate": "is_recommendation", "value": not repaired}]],
        "rationale": "Allow recommendations.",
      }
    )
  ambiguities: list[dict[str, object]] = []
  if bug_type == "ambiguity" and not repaired:
    ambiguities.append(
      {
        "rule_id": "rule:1",
        "category": "undefined_threshold",
        "excerpt": "substantial",
        "rationale": "No threshold is defined.",
        "interpretation_a": {
          "label": "all recommendations",
          "decision": "block",
          "condition": [[{"predicate": "is_recommendation", "value": True}]],
          "rationale": "Apply broadly.",
        },
        "interpretation_b": {
          "label": "non-recommendations",
          "decision": "block",
          "condition": [[{"predicate": "is_recommendation", "value": False}]],
          "rationale": "Apply narrowly.",
        },
      }
    )
  return json.dumps(
    {
      "predicates": {"is_recommendation": "content recommends an investment"},
      "safe_condition": [[{"predicate": "is_recommendation", "value": False}]],
      "rules": rules,
      "exclusions": [],
      "ambiguities": ambiguities,
    }
  )


@pytest.mark.parametrize("bug_type", ["over_strict", "contradiction", "ambiguity"])
def test_natural_proposed_fix_supports_each_finding_class(bug_type: str) -> None:
  rules = ["Block substantial investment advice."]
  if bug_type == "contradiction":
    rules.append("Allow investment advice.")
  rule_lines = "".join(f"  - {rule}\n" for rule in rules)
  text = f"""\
name: sample
system_prompt: Be helpful.
rules:
{rule_lines}examples:
  - content: Neutral report.
    should_block: false
    reasoning: Neutral.
  - content: You should invest.
    should_block: true
    reasoning: Recommendation.
"""
  target = target_config()
  baseline_backend = SequenceBackend([_natural_finding_model(bug_type, repaired=False)])
  baseline = bugs(text, model=target, backend_factory=lambda config: baseline_backend)
  finding = next(item for item in baseline.findings if item.bug_type == bug_type)
  candidate_response = json.dumps(
    {
      "target_rule_ids": list(finding.rule_ids),
      "replacement_rules": {
        rule_id: f"Clarify the intended boundary for {rule_id}."
        for rule_id in finding.rule_ids
      },
      "explanation": f"Repair the {bug_type} finding.",
    }
  )
  evaluation_response = json.dumps(
    {
      "results": [
        {"index": 0, "should_block": False, "rationale": "Neutral."},
        {"index": 1, "should_block": True, "rationale": "Recommendation."},
      ]
    }
  )
  backend = SequenceBackend(
    [
      _natural_finding_model(bug_type, repaired=False),
      candidate_response,
      _natural_finding_model(bug_type, repaired=True),
      _natural_finding_model(bug_type, repaired=True),
      evaluation_response,
      evaluation_response,
    ]
  )

  result = propose_fix(
    text,
    specification_name="guardrail.yaml",
    taxonomy_text=None,
    violations_text=None,
    workflow="scan",
    finding_id=finding.id,
    expected_content_hash=baseline.content_hash,
    target=target,
    backend_factory=lambda config: backend,
  )

  assert result.status == "validated", result.to_dict()


def test_diagnosis_proposed_fix_requires_violations() -> None:
  text = (EXAMPLES / "regexes.yaml").read_text(encoding="utf-8")

  with pytest.raises(ValueError, match="violations are required"):
    _ = propose_fix(
      text,
      specification_name="regexes.yaml",
      taxonomy_text=None,
      violations_text=None,
      workflow="diagnose",
      finding_id="finding",
      expected_content_hash="hash",
      target=target_config(),
    )


def test_regex_proposed_fix_with_an_invalid_pattern_is_an_invalid_candidate() -> None:
  text = (EXAMPLES / "regexes.yaml").read_text(encoding="utf-8")
  baseline = bugs(text, model=target_config())
  finding = next(item for item in baseline.findings if item.bug_type == "over_strict")
  manifest = json_object(cast("object", yaml.safe_load(text)))
  rules = {
    json_object(rule)["id"]: json_object(rule) for rule in json_list(manifest["rules"])
  }
  backend = SequenceBackend(
    [
      json.dumps(
        {
          "target_rule_ids": list(finding.rule_ids),
          "replacement_rules": {
            rule_id: {**rules[rule_id], "pattern": "(unclosed"}
            for rule_id in finding.rule_ids
          },
          "explanation": "Break the pattern.",
        }
      )
    ]
  )

  result = propose_fix(
    text,
    specification_name="regexes.yaml",
    taxonomy_text=None,
    violations_text=None,
    workflow="scan",
    finding_id=finding.id,
    expected_content_hash=baseline.content_hash,
    target=target_config(),
    backend_factory=lambda config: backend,
  )

  validity = next(
    check for check in result.validation_checks if check.code == "valid_specification"
  )
  assert result.status == "unavailable"
  assert validity.status == "failed"
  assert result.unavailable_reasons == ("invalid_candidate",)


OVER_STRICT_TEXT = """\
name: sample
system_prompt: Be helpful.
rules:
  - Block any mention of investing.
examples:
  - content: Neutral report.
    should_block: false
    reasoning: Neutral.
"""


NEUTRAL_EVALUATION = json.dumps(
  {"results": [{"index": 0, "should_block": False, "rationale": "Neutral."}]}
)


def _propose_over_strict_fix(
  revision_responses: list[str],
) -> tuple[ProposedFix, JudgeBackend]:
  target = target_config()
  baseline_backend = SequenceBackend([_symbolic_response(repaired=False)])
  baseline = bugs(
    OVER_STRICT_TEXT, model=target, backend_factory=lambda config: baseline_backend
  )
  candidate = json.dumps(
    {
      "target_rule_ids": ["rule:1"],
      "replacement_rules": {"rule:1": "Block investment recommendations."},
      "explanation": "Narrow the rule.",
    }
  )
  backend = SequenceBackend(
    [_symbolic_response(repaired=False), candidate, *revision_responses]
  )
  result = propose_fix(
    OVER_STRICT_TEXT,
    specification_name="sample.yaml",
    taxonomy_text=None,
    violations_text=None,
    workflow="scan",
    finding_id=baseline.findings[0].id,
    expected_content_hash=baseline.content_hash,
    target=target,
    backend_factory=lambda config: backend,
  )
  return result, backend


def test_natural_fix_that_leaves_the_target_finding_is_unavailable() -> None:
  unrepaired = _symbolic_response(repaired=False)

  result, _ = _propose_over_strict_fix(
    [unrepaired, unrepaired, NEUTRAL_EVALUATION, NEUTRAL_EVALUATION]
  )

  assert result.status == "unavailable"
  assert "target_finding_remains" in result.unavailable_reasons


def test_natural_fix_that_introduces_a_finding_is_unavailable() -> None:
  ambiguous = _natural_finding_model("ambiguity", repaired=False)

  result, _ = _propose_over_strict_fix(
    [ambiguous, ambiguous, NEUTRAL_EVALUATION, NEUTRAL_EVALUATION]
  )

  assert result.status == "unavailable"
  assert "new_findings" in result.unavailable_reasons


def test_natural_fix_with_an_unparseable_revision_extraction_raises() -> None:
  with pytest.raises(RepairGenerationError, match="semantic extraction"):
    _ = _propose_over_strict_fix(["not json"] * 6)


def test_natural_fix_with_incomplete_example_evaluation_raises() -> None:
  repaired = _symbolic_response(repaired=True)
  empty_evaluation = json.dumps({"results": []})

  with pytest.raises(RepairGenerationError, match="example evaluation"):
    _ = _propose_over_strict_fix([repaired, repaired, empty_evaluation])


@pytest.mark.parametrize(
  "overrides,message",
  [
    ({"expected_content_hash": "stale"}, "analysis inputs changed"),
    ({"finding_id": "missing"}, "finding is no longer present"),
  ],
)
def test_proposed_fix_rejects_stale_analysis_bindings(
  overrides: dict[str, str], message: str
) -> None:
  text = (EXAMPLES / "regexes.yaml").read_text(encoding="utf-8")
  baseline = bugs(text, model=target_config())
  arguments = {
    "finding_id": baseline.findings[0].id,
    "expected_content_hash": baseline.content_hash,
  }
  arguments.update(overrides)

  with pytest.raises(LookupError, match=message):
    _ = propose_fix(
      text,
      specification_name="regexes.yaml",
      taxonomy_text=None,
      violations_text=None,
      workflow="scan",
      finding_id=arguments["finding_id"],
      expected_content_hash=arguments["expected_content_hash"],
      target=target_config(),
    )
