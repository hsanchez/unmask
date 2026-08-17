import json
from pathlib import Path

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
