from pathlib import Path

import pytest

from tests.helpers import write_yaml
from unmask.diagnosis import RegexExample, RegexManifest, RegexRule
from unmask.scanning import (
  load_taxonomy_regex_examples,
  merge_regex_examples,
  scan_regex_manifest,
)


class TestLoadTaxonomyRegexExamples:
  def test_returns_empty_tuple_for_none_path(self) -> None:
    assert load_taxonomy_regex_examples(None) == ()

  def test_loads_examples_and_tags_source(self, tmp_path: Path) -> None:
    path = write_yaml(
      tmp_path / "taxonomy.yaml",
      {
        "regex_examples_version": 1,
        "regex_examples": [
          {
            "content": "hack this",
            "expected_action": "block",
            "reason": "matches the prohibited pattern",
          }
        ],
      },
    )
    examples = load_taxonomy_regex_examples(path)
    assert examples[0].source == "taxonomy"

  def test_rejects_wrong_version(self, tmp_path: Path) -> None:
    path = write_yaml(
      tmp_path / "taxonomy.yaml",
      {"regex_examples_version": 2, "regex_examples": []},
    )
    with pytest.raises(ValueError, match="regex_examples_version"):
      _ = load_taxonomy_regex_examples(path)

  def test_rejects_non_mapping_document(self, tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.yaml"
    _ = path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(TypeError, match="mapping"):
      _ = load_taxonomy_regex_examples(path)


class TestMergeRegexExamples:
  def test_prefers_manifest_examples_on_content_overlap(self) -> None:
    manifest_example = RegexExample(content="hack this", expected_action="block")
    taxonomy_example = RegexExample(
      content="hack this", expected_action="block", source="taxonomy"
    )
    merged = merge_regex_examples((manifest_example,), (taxonomy_example,))
    assert merged == (manifest_example,)

  def test_raises_on_conflicting_expected_actions(self) -> None:
    manifest_example = RegexExample(content="hack this", expected_action="block")
    taxonomy_example = RegexExample(
      content="hack this", expected_action="allow", source="taxonomy"
    )
    with pytest.raises(ValueError, match="conflicting expected actions"):
      _ = merge_regex_examples((manifest_example,), (taxonomy_example,))

  def test_appends_non_overlapping_taxonomy_examples(self) -> None:
    manifest_example = RegexExample(content="hack this", expected_action="block")
    taxonomy_example = RegexExample(
      content="totally different", expected_action="allow", source="taxonomy"
    )
    merged = merge_regex_examples((manifest_example,), (taxonomy_example,))
    assert merged == (manifest_example, taxonomy_example)


class TestScanRegexManifest:
  def test_scan_with_no_examples_reports_insufficient_evidence(self) -> None:
    manifest = RegexManifest(
      name="test", rules=(RegexRule(id="rule:1", pattern="hack", action="block"),)
    )
    report = scan_regex_manifest(manifest)
    assert report.cases == ()
    summary = report.to_dict()["summary"]
    assert isinstance(summary, dict)
    assert summary["status"] == "insufficient_evidence"

  def test_scan_diagnoses_manifest_examples(self) -> None:
    manifest = RegexManifest(
      name="test",
      rules=(RegexRule(id="rule:1", pattern="hack", action="block"),),
      examples=(
        RegexExample(
          content="please hack this",
          expected_action="block",
          reason="matches the prohibited pattern",
        ),
      ),
    )
    report = scan_regex_manifest(manifest)
    assert len(report.cases) == 1
    assert report.cases[0].example.content == "please hack this"
