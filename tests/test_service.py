import json
from pathlib import Path

import pytest
import yaml

from unmask.config import TargetConfig
from unmask.service import (
  diagnose_specification,
  scan_specification,
  validate_specification,
)

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class FakeBackend:
  def judge(self, prompt: str, *, max_tokens: int = 8192) -> str:
    del prompt, max_tokens
    return json.dumps(
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


def _target() -> TargetConfig:
  return TargetConfig(
    project=None,
    location="global",
    judge_model="fake",
    thinking_budget=0,
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
