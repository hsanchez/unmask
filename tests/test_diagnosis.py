from pathlib import Path
from typing import cast

import pytest

from tests.helpers import write_yaml
from unmask.diagnosis import (
  ConversationIncident,
  DiagnosisConfig,
  RegexDiagnoser,
  RegexInterpretation,
  RegexManifest,
  RegexRule,
  TextLocation,
)
from unmask.models import Decision


def _incident(
  text: str,
  *,
  expected_outcome: Decision | None = None,
  expected_reason: str | None = None,
) -> ConversationIncident:
  return ConversationIncident(
    text=text,
    value=text,
    location=TextLocation(start=0, end=len(text)),
    expected_outcome=expected_outcome,
    expected_reason=expected_reason,
  )


class TestTextLocation:
  def test_valid_location(self) -> None:
    location = TextLocation(start=0, end=3)
    assert location.to_dict() == {"start": 0, "end": 3}

  @pytest.mark.parametrize("start,end", [(-1, 3), (3, 3), (5, 3)])
  def test_rejects_invalid_range(self, start: int, end: int) -> None:
    with pytest.raises(ValueError, match="0 <= start < end"):
      _ = TextLocation(start=start, end=end)

  def test_rejects_non_integer(self) -> None:
    with pytest.raises(TypeError, match="must be integers"):
      _ = TextLocation(start=cast("int", cast("object", "0")), end=3)


class TestConversationIncident:
  def test_validate_rejects_value_text_mismatch(self) -> None:
    with pytest.raises(ValueError, match="does not match"):
      _ = ConversationIncident(
        text="hello world", value="goodbye", location=TextLocation(start=0, end=7)
      )

  def test_allows_outcome_without_reason(self) -> None:
    incident = _incident("hello", expected_outcome="allow")
    assert incident.expected_reason is None

  def test_requires_outcome_with_reason(self) -> None:
    with pytest.raises(ValueError, match="expected_outcome is required"):
      _ = _incident("hello", expected_reason="because")

  def test_from_dict_round_trips(self) -> None:
    raw = {
      "text": "please hack this",
      "value": "hack",
      "location": {"start": 7, "end": 11},
      "expected_outcome": "block",
      "expected_reason": "policy",
    }
    incident = ConversationIncident.from_dict(raw)
    assert incident.value == "hack"
    assert incident.expected_outcome_source == "violation"


class TestRegexRule:
  def test_rejects_invalid_pattern(self) -> None:
    with pytest.raises(ValueError, match="invalid regex"):
      _ = RegexRule(id="rule:1", pattern="(unclosed", action="block")

  def test_rejects_wrong_interpretation_count(self) -> None:
    interpretation = RegexInterpretation(label="a", pattern="hack", action="block")
    with pytest.raises(ValueError, match="exactly two entries"):
      _ = RegexRule.from_dict(
        raw={
          "id": "rule:1",
          "pattern": "hack",
          "action": "block",
          "interpretations": [interpretation.to_dict()],
        },
        index=0,
      )


class TestRegexManifest:
  def test_rejects_duplicate_rule_ids(self) -> None:
    with pytest.raises(ValueError, match="unique"):
      _ = RegexManifest(
        name="test",
        rules=(
          RegexRule(id="rule:1", pattern="a", action="block"),
          RegexRule(id="rule:1", pattern="b", action="allow"),
        ),
      )

  def test_from_yaml_round_trips(self, tmp_path: Path) -> None:
    path = write_yaml(
      tmp_path / "manifest.yaml",
      {
        "name": "test",
        "rules": [{"id": "rule:1", "pattern": "hack", "action": "block"}],
      },
    )
    manifest = RegexManifest.from_yaml(path)
    assert manifest.rules[0].id == "rule:1"


class TestDiagnosisConfig:
  def test_from_yaml_rejects_empty_rules(self, tmp_path: Path) -> None:
    path = write_yaml(
      tmp_path / "diagnosis.yaml",
      {"name": "test", "conversation": {}, "rules": []},
    )
    with pytest.raises(ValueError, match="non-empty list"):
      _ = DiagnosisConfig.from_yaml(path)

  def test_from_yaml_resolves_output_relative_to_source(self, tmp_path: Path) -> None:
    path = write_yaml(
      tmp_path / "diagnosis.yaml",
      {
        "name": "test",
        "conversation": {
          "text": "please hack this",
          "value": "hack",
          "location": {"start": 7, "end": 11},
        },
        "rules": [{"id": "rule:1", "pattern": "hack", "action": "block"}],
      },
    )
    config = DiagnosisConfig.from_yaml(path)
    assert config.output_path == tmp_path / "diagnosis.json"


class TestRegexDiagnoser:
  def test_over_strict_and_skipped_analyses(self, tmp_path: Path) -> None:
    config = DiagnosisConfig(
      source=tmp_path / "diagnosis.yaml",
      name="over-strict-case",
      incident=_incident(
        "please hack this puzzle",
        expected_outcome="allow",
        expected_reason="it's a puzzle reference",
      ),
      rules=(RegexRule(id="rule:1", pattern="hack", action="block"),),
      output_path=tmp_path / "diagnosis.json",
    )
    report = RegexDiagnoser().diagnose(config)
    assert [finding.bug_type for finding in report.findings] == ["over_strict"]
    assert any(
      skipped.bug_type == "contradiction" for skipped in report.skipped_analyses
    )
    assert any(skipped.bug_type == "ambiguity" for skipped in report.skipped_analyses)

  def test_contradiction_between_overlapping_rules(self, tmp_path: Path) -> None:
    config = DiagnosisConfig(
      source=tmp_path / "diagnosis.yaml",
      name="contradiction-case",
      incident=_incident("please help me hack this"),
      rules=(
        RegexRule(id="rule:block", pattern="hack", action="block"),
        RegexRule(id="rule:allow", pattern="help me hack", action="allow"),
      ),
      output_path=tmp_path / "diagnosis.json",
    )
    report = RegexDiagnoser().diagnose(config)
    assert [finding.bug_type for finding in report.findings] == ["contradiction"]
    assert report.findings[0].rule_ids == ("rule:allow", "rule:block")

  def test_ambiguity_when_interpretations_diverge(self, tmp_path: Path) -> None:
    config = DiagnosisConfig(
      source=tmp_path / "diagnosis.yaml",
      name="ambiguity-case",
      incident=_incident("please hack this"),
      rules=(
        RegexRule(
          id="rule:1",
          pattern="hack",
          action="block",
          interpretations=(
            RegexInterpretation(label="any use", pattern="hack", action="block"),
            RegexInterpretation(
              label="malicious only", pattern="hack the mainframe", action="allow"
            ),
          ),
        ),
      ),
      output_path=tmp_path / "diagnosis.json",
    )
    report = RegexDiagnoser().diagnose(config)
    assert [finding.bug_type for finding in report.findings] == ["ambiguity"]

  def test_no_matches_reports_no_matching_rule_status(self, tmp_path: Path) -> None:
    config = DiagnosisConfig(
      source=tmp_path / "diagnosis.yaml",
      name="no-match-case",
      incident=_incident("this text is entirely benign"),
      rules=(RegexRule(id="rule:1", pattern="hack", action="block"),),
      output_path=tmp_path / "diagnosis.json",
    )
    report = RegexDiagnoser().diagnose(config)
    assert report.matches == ()
    assert report.findings == ()
    summary = report.to_dict()["summary"]
    assert isinstance(summary, dict)
    assert summary["status"] == "no_matching_rule"
