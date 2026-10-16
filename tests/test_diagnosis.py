from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from tests.helpers import write_yaml
from unmask.diagnosis import (
  ConversationIncident,
  DiagnosisConfig,
  RegexDiagnoser,
  RegexExample,
  RegexExecutionError,
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


def _rule_data(**overrides: object) -> dict[str, object]:
  rule: dict[str, object] = {"id": "rule:1", "pattern": "hack", "action": "block"}
  rule.update(overrides)
  return rule


def _incident_data(**overrides: object) -> dict[str, object]:
  incident: dict[str, object] = {
    "text": "please hack this",
    "value": "hack",
    "location": {"start": 7, "end": 11},
  }
  incident.update(overrides)
  return incident


def _manifest_data(**overrides: object) -> dict[str, object]:
  manifest: dict[str, object] = {"name": "sample", "rules": [_rule_data()]}
  manifest.update(overrides)
  return manifest


def _config(
  tmp_path: Path,
  text: str,
  rules: tuple[RegexRule, ...],
  *,
  expected_outcome: Decision | None = None,
  expected_reason: str | None = None,
) -> DiagnosisConfig:
  return DiagnosisConfig(
    source=tmp_path / "diagnosis.yaml",
    name="case",
    incident=_incident(
      text, expected_outcome=expected_outcome, expected_reason=expected_reason
    ),
    rules=rules,
    output_path=tmp_path / "diagnosis.json",
  )


class TestSchemaValidation:
  @pytest.mark.parametrize(
    "raw,error,message",
    [
      ({"start": "0", "end": 3}, TypeError, "must be integers"),
      ({"start": 0}, TypeError, "must be integers"),
      ([0, 3], TypeError, "conversation.location must be a mapping"),
    ],
  )
  def test_text_location_rejects_invalid_documents(
    self, raw: object, error: type[Exception], message: str
  ) -> None:
    with pytest.raises(error, match=message):
      _ = TextLocation.from_dict(raw)

  @pytest.mark.parametrize(
    "overrides,error,message",
    [
      ({"text": ""}, ValueError, "conversation.text must be a non-empty string"),
      ({"expected_outcome": "maybe"}, ValueError, "must be 'allow' or 'block'"),
      (
        {"expected_outcome": "allow", "expected_reason": " "},
        ValueError,
        "expected_reason must be a non-empty string",
      ),
      (
        {"expected_reason": "why"},
        ValueError,
        "expected_outcome is required when expected_reason",
      ),
      (
        {"expected_outcome": "allow", "expected_outcome_source": "guess"},
        ValueError,
        "expected_outcome_source must be",
      ),
      (
        {"expected_outcome_source": "manifest"},
        ValueError,
        "expected_outcome_source must be missing",
      ),
      ({"source_location": -1}, ValueError, "source_location must be a non-negative"),
      ({"source_location": "7"}, ValueError, "source_location must be a non-negative"),
      (
        {"location": {"start": 7, "end": 99}},
        ValueError,
        "location exceeds conversation.text",
      ),
    ],
  )
  def test_conversation_incident_rejects_invalid_documents(
    self, overrides: dict[str, object], error: type[Exception], message: str
  ) -> None:
    with pytest.raises(error, match=message):
      _ = ConversationIncident.from_dict(_incident_data(**overrides))

  @pytest.mark.parametrize("source_location", [-1, True])
  def test_conversation_incident_rejects_invalid_source_locations(
    self, source_location: int
  ) -> None:
    with pytest.raises(ValueError, match="source_location must be a non-negative"):
      _ = ConversationIncident(
        text="hack",
        value="hack",
        location=TextLocation(0, 4),
        source_location=source_location,
      )

  @pytest.mark.parametrize(
    "overrides,error,message",
    [
      ({"flags": ["MULTILINE", "BOGUS"]}, ValueError, "unsupported flag 'BOGUS'"),
      ({"flags": "I"}, TypeError, "flags must be a list"),
      ({"action": None}, ValueError, "must be 'allow' or 'block'"),
      ({"description": 3}, TypeError, "description must be a string"),
      (
        {"interpretations": [{"label": "a", "pattern": "x", "action": "block"}]},
        ValueError,
        "exactly two entries",
      ),
    ],
  )
  def test_regex_rule_rejects_invalid_documents(
    self, overrides: dict[str, object], error: type[Exception], message: str
  ) -> None:
    with pytest.raises(error, match=message):
      _ = RegexRule.from_dict(_rule_data(**overrides), index=0)

  def test_regex_rule_serializes_optional_fields_and_removes_duplicate_flags(
    self,
  ) -> None:
    rule = RegexRule.from_dict(
      _rule_data(flags=["IGNORECASE", "IGNORECASE"], description=" Reason "), index=0
    )

    assert rule.to_dict() == {
      "id": "rule:1",
      "pattern": "hack",
      "action": "block",
      "flags": ["IGNORECASE"],
      "description": "Reason",
    }

  def test_regex_example_rejects_an_unknown_source(self) -> None:
    raw = {"content": "a", "expected_action": "allow", "reason": "r", "source": "x"}

    with pytest.raises(ValueError, match="source must be 'manifest' or 'taxonomy'"):
      _ = RegexExample.from_dict(raw, index=0)

  @pytest.mark.parametrize(
    "overrides,error,message",
    [
      ({"schema_version": 2}, ValueError, "schema_version must be 1"),
      ({"schema_version": "1"}, TypeError, "schema_version must be an integer"),
      ({"kind": "natural"}, ValueError, "kind must be 'regex'"),
      ({"engine": "re2"}, ValueError, "engine must be 'python_re'"),
      ({"scope": "user_input"}, ValueError, "scope must be 'agent_output'"),
      ({"name": ""}, ValueError, "name must be a non-empty string"),
      ({"rules": []}, ValueError, "rules must be a non-empty list"),
      ({"rules": {}}, TypeError, "rules must be a list"),
      (
        {"rules": [_rule_data(), _rule_data()]},
        ValueError,
        "rule IDs must be unique",
      ),
      (
        {
          "examples": [
            {"content": "a", "expected_action": "allow", "reason": "r"},
            {"content": "a", "expected_action": "block", "reason": "r"},
          ]
        },
        ValueError,
        "conflicting expected actions",
      ),
      (
        {
          "examples": [
            {
              "content": "a",
              "expected_action": "allow",
              "reason": "r",
              "source": "taxonomy",
            }
          ]
        },
        ValueError,
        "must be 'manifest' in a regex manifest",
      ),
      ({"match_timeout_seconds": 0}, ValueError, "match_timeout_seconds must be"),
      ({"match_timeout_seconds": True}, ValueError, "match_timeout_seconds must be"),
    ],
  )
  def test_regex_manifest_rejects_invalid_documents(
    self, overrides: dict[str, object], error: type[Exception], message: str
  ) -> None:
    with pytest.raises(error, match=message):
      _ = RegexManifest.from_dict(_manifest_data(**overrides))

  @pytest.mark.parametrize(
    "overrides,message",
    [
      ({"schema_version": 2}, "schema_version must be 1"),
      ({"kind": "natural"}, "kind must be 'regex'"),
      ({"engine": "re2"}, "engine must be 'python_re'"),
      ({"scope": "user_input"}, "scope must be 'agent_output'"),
      ({"name": " "}, "name must be a non-empty string"),
      ({"rules": ()}, "rules must be a non-empty list"),
    ],
  )
  def test_regex_manifest_constructor_enforces_the_same_invariants(
    self, overrides: dict[str, object], message: str
  ) -> None:
    valid = RegexManifest.from_dict(_manifest_data())

    with pytest.raises(ValueError, match=message):
      _ = replace(valid, **overrides)

  def test_regex_manifest_serializes_examples(self) -> None:
    manifest = RegexManifest.from_dict(
      _manifest_data(
        examples=[{"content": "a", "expected_action": "allow", "reason": "r"}]
      )
    )

    assert manifest.to_dict()["examples"] == [
      {"content": "a", "expected_action": "allow", "reason": "r", "source": "manifest"}
    ]

  @pytest.mark.parametrize(
    "overrides,message",
    [
      ({"rules": [_rule_data(), _rule_data()]}, "rule IDs must be unique"),
      ({"output": "  "}, "output must be a non-empty path string"),
      ({"output": 3}, "output must be a non-empty path string"),
    ],
  )
  def test_diagnosis_config_rejects_invalid_documents(
    self, tmp_path: Path, overrides: dict[str, object], message: str
  ) -> None:
    document: dict[str, object] = {
      "name": "test",
      "conversation": _incident_data(),
      "rules": [_rule_data()],
    }
    document.update(overrides)
    path = write_yaml(tmp_path / "diagnosis.yaml", document)

    with pytest.raises(ValueError, match=message):
      _ = DiagnosisConfig.from_yaml(path)


class TestDiagnosisBranches:
  def test_policy_reason_is_required_to_establish_over_strictness(
    self, tmp_path: Path
  ) -> None:
    config = _config(
      tmp_path,
      "please hack this",
      (RegexRule(id="rule:1", pattern="hack", action="block"),),
      expected_outcome="allow",
    )

    report = RegexDiagnoser().diagnose(config)

    assert report.findings == ()
    assert any(
      "a policy reason is required" in skipped.reason
      for skipped in report.skipped_analyses
    )

  def test_over_strict_check_ignores_allow_rules_and_unmatched_blocks(
    self, tmp_path: Path
  ) -> None:
    config = _config(
      tmp_path,
      "please hack this",
      (
        RegexRule(id="rule:allow", pattern="hack", action="allow"),
        RegexRule(id="rule:unmatched", pattern="absent", action="block"),
      ),
      expected_outcome="allow",
      expected_reason="Neutral.",
    )

    assert RegexDiagnoser().diagnose(config).findings == ()

  def test_contradiction_check_ignores_same_action_and_disjoint_matches(
    self, tmp_path: Path
  ) -> None:
    config = _config(
      tmp_path,
      "hack and trade",
      (
        RegexRule(id="rule:1", pattern="hack", action="block"),
        RegexRule(id="rule:2", pattern="hack", action="block"),
        RegexRule(id="rule:3", pattern="trade", action="allow"),
      ),
    )

    assert RegexDiagnoser().diagnose(config).findings == ()

  def test_ambiguity_check_ignores_interpretations_that_agree(
    self, tmp_path: Path
  ) -> None:
    rule = RegexRule(
      id="rule:1",
      pattern="hack",
      action="block",
      interpretations=(
        RegexInterpretation(label="broad", pattern="hack", action="block"),
        RegexInterpretation(label="narrow", pattern="hack this", action="block"),
      ),
    )

    report = RegexDiagnoser().diagnose(_config(tmp_path, "please hack this", (rule,)))

    assert report.findings == ()

  def test_long_incident_context_is_truncated_on_both_sides(
    self, tmp_path: Path
  ) -> None:
    text = f"{'a' * 120} hack {'b' * 120}"
    config = _config(
      tmp_path,
      text,
      (
        RegexRule(id="rule:block", pattern="hack", action="block"),
        RegexRule(id="rule:allow", pattern="hack", action="allow"),
      ),
    )
    start = text.index("hack")
    config = DiagnosisConfig(
      source=config.source,
      name=config.name,
      incident=ConversationIncident(
        text=text, value="hack", location=TextLocation(start, start + 4)
      ),
      rules=config.rules,
      output_path=config.output_path,
    )

    finding = RegexDiagnoser().diagnose(config).findings[0]

    serialized = str(finding.to_dict())
    assert "'..." in serialized
    assert "...'" in serialized


class TestRegexWorkerLimits:
  def test_catastrophic_backtracking_times_out(self, tmp_path: Path) -> None:
    text = f"{'a' * 40}b"
    config = DiagnosisConfig(
      source=tmp_path / "diagnosis.yaml",
      name="timeout",
      incident=_incident(text),
      rules=(RegexRule(id="rule:slow", pattern="(a+)+$", action="block"),),
      output_path=tmp_path / "diagnosis.json",
      match_timeout_seconds=0.2,
    )

    with pytest.raises(RegexExecutionError, match="rule:slow exceeded the 0.2-second"):
      _ = RegexDiagnoser().diagnose(config)

  def test_excessive_matches_are_reported_as_an_error(self, tmp_path: Path) -> None:
    config = _config(
      tmp_path,
      "a" * 10_001,
      (RegexRule(id="rule:flood", pattern="a", action="block"),),
    )

    with pytest.raises(RegexExecutionError, match="more than 10000 relevant matches"):
      _ = RegexDiagnoser().diagnose(config)
