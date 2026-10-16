import json
from pathlib import Path
from typing import cast

import pytest

from unmask.diagnosis import (
  ConversationIncident,
  DiagnosisConfig,
  RegexDiagnoser,
  RegexRule,
  TextLocation,
)
from unmask.visualization import (
  CollectionResult,
  DiagnosisVisualizer,
  render_collection_index,
  write_visualization,
)


def _report_with_over_strict_finding(tmp_path: Path) -> dict[str, object]:
  text = "please hack this puzzle"
  config = DiagnosisConfig(
    source=tmp_path / "diagnosis.yaml",
    name="over-strict-case",
    incident=ConversationIncident(
      text=text,
      value=text,
      location=TextLocation(start=0, end=len(text)),
      expected_outcome="allow",
      expected_reason="it's a puzzle reference",
    ),
    rules=(RegexRule(id="rule:1", pattern="hack", action="block"),),
    output_path=tmp_path / "diagnosis.json",
  )
  return RegexDiagnoser().diagnose(config).to_dict()


def _report_with_no_findings(tmp_path: Path) -> dict[str, object]:
  text = "this text is entirely benign"
  config = DiagnosisConfig(
    source=tmp_path / "diagnosis.yaml",
    name="no-match-case",
    incident=ConversationIncident(
      text=text,
      value=text,
      location=TextLocation(start=0, end=len(text)),
    ),
    rules=(RegexRule(id="rule:1", pattern="hack", action="block"),),
    output_path=tmp_path / "diagnosis.json",
  )
  return RegexDiagnoser().diagnose(config).to_dict()


class TestDiagnosisVisualizerRender:
  def test_renders_findings_as_html(self, tmp_path: Path) -> None:
    html = DiagnosisVisualizer().render(_report_with_over_strict_finding(tmp_path))
    assert "<html" in html
    assert "hack" in html

  def test_renders_unproven_report_as_html(self, tmp_path: Path) -> None:
    html = DiagnosisVisualizer().render(_report_with_no_findings(tmp_path))
    assert "No supported defect proven" in html

  def test_rejects_non_mapping(self) -> None:
    with pytest.raises(TypeError, match="mapping"):
      _ = DiagnosisVisualizer().render("not-a-mapping")

  def test_render_file_loads_json(self, tmp_path: Path) -> None:
    report = _report_with_no_findings(tmp_path)
    report_path = tmp_path / "report.json"
    _ = report_path.write_text(json.dumps(report), encoding="utf-8")
    html = DiagnosisVisualizer().render_file(report_path)
    assert "<html" in html


class TestWriteVisualization:
  def test_creates_parent_directories(self, tmp_path: Path) -> None:
    output = write_visualization(tmp_path / "nested" / "report.html", "<html></html>")
    assert output.read_text(encoding="utf-8") == "<html></html>"


class TestRenderCollectionIndex:
  def test_renders_rows_for_each_result(self) -> None:
    result: CollectionResult = {
      "incident": 1,
      "sourceLocation": 0,
      "value": "hack",
      "findings": 1,
      "status": "supported_defect",
      "expectedOutcomeSource": "manifest",
      "expectedReason": None,
      "config": "config.yaml",
      "report": "report.json",
      "visualization": "output/1/report.html",
    }
    html = render_collection_index([result])
    assert "<table>" in html
    assert "Open report" in html
    assert 'href="output/1/report.html"' in html

  def test_renders_unavailable_when_no_visualization(self) -> None:
    result: CollectionResult = {
      "incident": 1,
      "sourceLocation": 0,
      "value": "hack",
      "findings": 0,
      "status": "no_matching_rule",
      "expectedOutcomeSource": "missing",
      "expectedReason": None,
      "config": "config.yaml",
      "report": "report.json",
      "visualization": None,
    }
    html = render_collection_index([result])
    assert "Unavailable" in html


class TestDiagnosisVisualizerMalformedReports:
  def _first_finding(self, report: dict[str, object]) -> dict[str, object]:
    findings = cast("list[object]", report["findings"])
    finding = findings[0]
    assert isinstance(finding, dict)
    return cast("dict[str, object]", finding)

  def test_rejects_a_non_list_field(self, tmp_path: Path) -> None:
    report = _report_with_over_strict_finding(tmp_path)
    report["findings"] = "not-a-list"

    with pytest.raises(TypeError, match="must be a list"):
      _ = DiagnosisVisualizer().render(report)

  def test_rejects_a_non_string_field(self, tmp_path: Path) -> None:
    report = _report_with_over_strict_finding(tmp_path)
    self._first_finding(report)["summary"] = 3

    with pytest.raises(TypeError, match="must be a string"):
      _ = DiagnosisVisualizer().render(report)

  def test_rejects_a_witness_that_is_not_boolean(self, tmp_path: Path) -> None:
    report = _report_with_over_strict_finding(tmp_path)
    proof = cast("dict[str, object]", self._first_finding(report)["proof"])
    proof["witness"] = {"predicate": "yes"}

    with pytest.raises(TypeError, match="witness must map strings to booleans"):
      _ = DiagnosisVisualizer().render(report)

  def test_rejects_evidence_with_non_integer_locations(self, tmp_path: Path) -> None:
    report = _report_with_over_strict_finding(tmp_path)
    evidence = cast("list[dict[str, object]]", self._first_finding(report)["evidence"])
    evidence[0]["location"] = {"start": "0", "end": 4}

    with pytest.raises(TypeError, match="start and end must be integers"):
      _ = DiagnosisVisualizer().render(report)

  def test_rejects_evidence_beyond_the_incident_text(self, tmp_path: Path) -> None:
    report = _report_with_over_strict_finding(tmp_path)
    evidence = cast("list[dict[str, object]]", self._first_finding(report)["evidence"])
    evidence[0]["location"] = {"start": 0, "end": 999}

    with pytest.raises(ValueError, match="exceeds incident text"):
      _ = DiagnosisVisualizer().render(report)
