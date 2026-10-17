import json

import pytest
import yaml

from tests.helpers import EXAMPLES, ConflictingRulesBackend, target_config
from unmask.service import (
  bugs,
  classify_specification,
  diagnose_specification,
  validate_specification,
)


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

  result = bugs(
    text,
    model=target_config(),
    backend_factory=lambda config: ConflictingRulesBackend(),
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

  result = bugs(text, model=target_config())

  assert result.specification_kind == "regex"
  assert len(result.findings) == 1
  assert result.findings[0].source_references[0].structural_unit_id == (
    "specification:rule:1"
  )


def test_regex_diagnosis_normalizes_incident_findings() -> None:
  result = diagnose_specification(
    (EXAMPLES / "regexes.yaml").read_text(encoding="utf-8"),
    (EXAMPLES / "violations.json").read_text(encoding="utf-8"),
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


@pytest.mark.parametrize(
  "text,error,message",
  [
    ("- a\n- b\n", TypeError, "must be a YAML mapping"),
    ("kind: other\nrules: []\n", ValueError, "unsupported specification kind: 'other'"),
    ("name: sample\nrules: []\n", ValueError, "must be a regex manifest"),
  ],
)
def test_classification_rejects_unsupported_documents(
  text: str, error: type[Exception], message: str
) -> None:
  with pytest.raises(error, match=message):
    _ = classify_specification(text)


def test_diagnosis_without_a_matching_rule_reports_no_matching_rule() -> None:
  specification = (EXAMPLES / "regexes.yaml").read_text(encoding="utf-8")
  violations = json.dumps(
    {
      "violations": [
        {"context": "entirely unrelated words", "value": "unrelated", "location": 9}
      ]
    }
  )

  result = diagnose_specification(specification, violations)

  assert result.summary["status"] == "no_matching_rule"


def test_diagnosis_with_matching_rules_and_no_defect_reports_no_supported_defect() -> (
  None
):
  specification = """\
kind: regex
name: sample
rules:
  - id: block-hack
    action: block
    pattern: hack
"""
  violations = json.dumps(
    {
      "violations": [
        {
          "context": "please hack now",
          "value": "hack",
          "location": 7,
          "expected_outcome": "block",
          "expected_reason": "The rule is meant to block this.",
        }
      ]
    }
  )

  result = diagnose_specification(specification, violations)

  assert result.summary["status"] == "no_supported_defect"
