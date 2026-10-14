import itertools
import json
from pathlib import Path

import pytest
import yaml

from unmask.backend import JudgeResponse
from unmask.config import TargetConfig
from unmask.diagnosis import RegexManifest
from unmask.scanning import RegexScanReport
from unmask.service import (
  _ObservedBackend,
  _regex_examples_retained,
  diagnose_specification,
  propose_fix,
  scan_specification,
  validate_specification,
)

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_repair_deadline_prevents_another_provider_call() -> None:
  backend = _ObservedBackend(
    FakeBackend(),
    target=TargetConfig(
      project=None, location="global", judge_model="test", thinking_budget=None
    ),
    call_numbers=itertools.count(1),
    deadline=0,
  )

  with pytest.raises(RuntimeError, match="exceeded its time budget"):
    _ = backend.judge("prompt")


class FakeBackend:
  def judge(self, prompt: str, *, max_tokens: int = 8192) -> JudgeResponse:
    del prompt, max_tokens
    return JudgeResponse(
      json.dumps(
        {
          "predicates": {"is_safe": "request is safe"},
          "safe_condition": [[{"predicate": "is_safe", "value": True}]],
          "rules": [
            {
              "rule_id": "rule:1",
              "decision": "block",
              "condition": [[{"predicate": "is_safe", "value": True}]],
              "rationale": "The rule blocks safe content.",
            },
            {
              "rule_id": "rule:2",
              "decision": "allow",
              "condition": [[{"predicate": "is_safe", "value": True}]],
              "rationale": "The rule allows safe content.",
            },
          ],
          "ambiguities": [
            {
              "rule_id": "rule:1",
              "category": "scope",
              "excerpt": "all advice",
              "rationale": "The scope is unclear.",
              "interpretation_a": {
                "label": "broad",
                "decision": "block",
                "condition": [[{"predicate": "is_safe", "value": True}]],
                "rationale": "All content is included.",
              },
              "interpretation_b": {
                "label": "narrow",
                "decision": "block",
                "condition": [[{"predicate": "is_safe", "value": False}]],
                "rationale": "Only unsafe content is included.",
              },
            }
          ],
        }
      )
    )


class SequenceBackend:
  def __init__(self, responses: list[str]) -> None:
    self.responses = responses

  def judge(self, prompt: str, *, max_tokens: int = 8192) -> JudgeResponse:
    del prompt, max_tokens
    if not self.responses:
      raise AssertionError("unexpected backend call")
    return JudgeResponse(self.responses.pop(0))


def _target() -> TargetConfig:
  return TargetConfig(
    project=None,
    location="global",
    judge_model="fake",
    thinking_budget=0,
  )


def test_empty_regex_example_set_is_vacuously_retained() -> None:
  manifest = RegexManifest.from_text(
    "schema_version: 1\nkind: regex\nname: sample\nengine: python_re\n"
    "scope: agent_output\nrules:\n"
    "  - id: block-hack\n    action: block\n    pattern: hack\n"
  )
  report = RegexScanReport(manifest=manifest, cases=(), taxonomy_supplied=False)

  assert _regex_examples_retained(report, report, target_example_hash=None) == (True, 0)


@pytest.mark.parametrize(
  "rule_source",
  [
    "Block all advice.",
    "'Block all advice.'",
    '"Block all advice."',
    "|\n    Block all advice.\n    Explain why.",
    ">-\n    Block all advice.\n    Explain why.",
  ],
)
def test_natural_rule_source_ranges_preserve_yaml_syntax(rule_source: str) -> None:
  text = f"name: sample\nsystem_prompt: Be helpful.\nrules:\n  - {rule_source}\n"

  result = validate_specification(text)

  unit = result.document.structural_units[0]
  selected = text[unit.start : unit.end]
  assert "Block all advice." in selected
  assert unit.rule_id == "rule:1"


def test_source_ranges_support_crlf_and_non_bmp_unicode() -> None:
  text = (
    "name: sample\r\nsystem_prompt: Be helpful.\r\nrules:\r\n  - Block 🚫 advice.\r\n"
  )

  result = validate_specification(text)

  unit = result.document.structural_units[0]
  assert text[unit.start : unit.end] == "Block 🚫 advice."


def test_source_ranges_preserve_combining_marks() -> None:
  text = (
    "name: sample\nsystem_prompt: Be helpful.\nrules:\n  - Block cafe\u0301 advice.\n"
  )

  result = validate_specification(text)

  unit = result.document.structural_units[0]
  assert text[unit.start : unit.end] == "Block cafe\u0301 advice."


def test_regex_rule_source_range_uses_manifest_rule_id() -> None:
  text = (
    "kind: regex\nname: sample\nengine: python_re\nscope: agent_output\n"
    "rules:\n  - id: block-hack\n    action: block\n    pattern: hack\n"
  )

  result = validate_specification(text)

  unit = result.document.structural_units[0]
  assert unit.rule_id == "block-hack"
  assert "id: block-hack" in text[unit.start : unit.end]


def test_natural_scan_normalizes_findings_and_source_references() -> None:
  text = (
    "name: sample\n"
    "system_prompt: Be helpful.\n"
    "rules:\n"
    "  - Block all advice.\n"
    "  - Allow safe advice.\n"
  )

  result = scan_specification(
    text,
    target=_target(),
    backend_factory=lambda config: FakeBackend(),
  )

  assert result.specification_kind == "natural_language"
  assert {finding.bug_type for finding in result.findings} == {
    "ambiguity",
    "contradiction",
    "over_strict",
  }
  contradiction = next(
    finding for finding in result.findings if finding.bug_type == "contradiction"
  )
  assert len(contradiction.source_references) == 2
  assert result.to_dict()["contentHash"] == result.content_hash


def test_regex_scan_normalizes_case_finding_source_reference() -> None:
  text = (
    "schema_version: 1\nkind: regex\nname: sample\nengine: python_re\n"
    "scope: agent_output\nrules:\n  - id: block-hack\n    action: block\n"
    "    pattern: hack\nexamples:\n  - content: harmless hack report\n"
    "    expected_action: allow\n    reason: neutral reporting\n"
  )

  result = scan_specification(text, target=_target())

  assert result.specification_kind == "regex"
  assert len(result.findings) == 1
  assert result.findings[0].source_references[0].structural_unit_id == (
    "specification:rule:1"
  )


def test_regex_diagnosis_normalizes_incident_findings() -> None:
  result = diagnose_specification(
    (_EXAMPLES / "regexes.yaml").read_text(encoding="utf-8"),
    (_EXAMPLES / "violations.json").read_text(encoding="utf-8"),
  )

  assert result.workflow == "diagnose"
  assert result.summary["incidents"] == 3
  assert {finding.bug_type for finding in result.findings} == {
    "ambiguity",
    "contradiction",
    "over_strict",
  }
  assert all(finding.metadata["remediation"] for finding in result.findings)
  assert all(
    finding.metadata["counterexampleExplanation"] for finding in result.findings
  )
  assert result.to_dict()["workflow"] == "diagnose"


def test_diagnosis_rejects_natural_language_specifications() -> None:
  with pytest.raises(ValueError, match="regex specification"):
    _ = diagnose_specification(
      "name: sample\nsystem_prompt: help\nrules:\n  - Block harm.\n",
      '{"violations":[{"context":"hack now","location":0,"value":"hack"}]}',
    )


@pytest.mark.parametrize("text", ["", "- item\n", "kind: unknown\n"])
def test_validation_rejects_unsupported_documents(text: str) -> None:
  with pytest.raises((TypeError, ValueError)):
    _ = validate_specification(text)


def test_validation_reports_malformed_yaml() -> None:
  with pytest.raises(yaml.YAMLError):
    _ = validate_specification("name: [unterminated\n")


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
  baseline = scan_specification(text, target=_target())
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
    target=_target(),
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
    target=_target(),
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
    target=_target(),
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
    target=_target(),
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
  baseline = scan_specification(text, target=_target())
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
    target=_target(),
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
  baseline = scan_specification(text, target=_target())
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
    target=_target(),
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
  text = (_EXAMPLES / "regexes.yaml").read_text(encoding="utf-8")
  baseline = scan_specification(text, target=_target())
  finding = next(item for item in baseline.findings if item.bug_type == bug_type)
  manifest = yaml.safe_load(text)
  manifest_rules = {rule["id"]: rule for rule in manifest["rules"]}
  replacements = {
    rule_id: dict(manifest_rules[rule_id]) for rule_id in finding.rule_ids
  }
  if bug_type == "over_strict":
    replacements["block-investment-term"]["pattern"] = r"\AYou should invest\b"
  elif bug_type == "contradiction":
    replacements["allow-how-to-education"]["pattern"] = r"(?<=how to )trade\b(?! Acme)"
  else:
    replacement = replacements["first-time-investor-scope"]
    replacement["interpretations"][1]["pattern"] = replacement["interpretations"][0][
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
    target=_target(),
    backend_factory=lambda config: backend,
  )

  assert result.status == "validated", result.to_dict()


def test_natural_proposed_fix_requires_trusted_examples() -> None:
  text = (
    "name: sample\nsystem_prompt: Be helpful.\nrules:\n"
    "  - Block all advice.\n  - Allow safe advice.\n"
  )
  baseline = scan_specification(
    text, target=_target(), backend_factory=lambda config: FakeBackend()
  )
  backend = FakeBackend()

  result = propose_fix(
    text,
    specification_name="sample.yaml",
    taxonomy_text=None,
    violations_text=None,
    workflow="scan",
    finding_id=baseline.findings[0].id,
    expected_content_hash=baseline.content_hash,
    target=_target(),
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
  target = _target()
  baseline_backend = SequenceBackend([_symbolic_response(repaired=False)])
  baseline = scan_specification(
    text, target=target, backend_factory=lambda config: baseline_backend
  )
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
  target = _target()
  baseline_backend = SequenceBackend([_symbolic_response(repaired=False)])
  baseline = scan_specification(
    text, target=target, backend_factory=lambda config: baseline_backend
  )
  candidate_response = json.dumps(
    {
      "target_rule_ids": ["rule:1"],
      "replacement_rules": {"rule:1": "Block investment recommendations."},
      "explanation": "Narrow the rule.",
    }
  )
  changed_model = json.loads(_symbolic_response(repaired=True))
  changed_model["predicates"]["is_recommendation"] = "content mentions an investment"
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
  ambiguities = []
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
  text = (
    "name: sample\nsystem_prompt: Be helpful.\nrules:\n"
    + "".join(f"  - {rule}\n" for rule in rules)
    + "examples:\n"
    "  - content: Neutral report.\n    should_block: false\n"
    "    reasoning: Neutral.\n"
    "  - content: You should invest.\n    should_block: true\n"
    "    reasoning: Recommendation.\n"
  )
  target = _target()
  baseline_backend = SequenceBackend([_natural_finding_model(bug_type, repaired=False)])
  baseline = scan_specification(
    text, target=target, backend_factory=lambda config: baseline_backend
  )
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
