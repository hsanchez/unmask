import json
from pathlib import Path
from typing import cast

import pytest
import yaml

from unmask.diagnosis import RegexManifest, RegexRule
from unmask.importer import (
  ImportedRegex,
  ImportedViolation,
  PythonRegexImporter,
  build_diagnosis_input,
  build_manifest_diagnosis_input,
  imported_regex_manifest,
  load_violations,
  load_violations_text,
  write_diagnosis_input,
  write_regex_manifest,
)

_VALID_SOURCE = """
_PATTERN_ONE = "foo"
_PATTERN_TWO = "bar" + "baz"
_PATTERN_THREE = f"{_PATTERN_ONE}-suffix"
PROHIBITED_PATTERNS = _compile_patterns(_PATTERN_ONE, _PATTERN_TWO, _PATTERN_THREE)
"""


class TestImportedRegex:
  def test_rule_id_strips_prefix_and_dashifies(self) -> None:
    regex = ImportedRegex(name="_PATTERN_ONE", pattern="foo")
    assert regex.rule_id == "pattern-one"


class TestImportedViolation:
  def test_local_start_finds_unique_occurrence(self) -> None:
    violation = ImportedViolation(
      context="please hack this", source_location=0, value="hack"
    )
    assert violation.local_start == 7

  def test_local_start_raises_when_missing(self) -> None:
    violation = ImportedViolation(context="hello", source_location=0, value="hack")
    with pytest.raises(ValueError, match="does not occur"):
      _ = violation.local_start

  def test_local_start_raises_when_ambiguous(self) -> None:
    violation = ImportedViolation(
      context="hack then hack again", source_location=0, value="hack"
    )
    with pytest.raises(ValueError, match="more than once"):
      _ = violation.local_start


class TestPythonRegexImporter:
  def test_loads_string_constants_and_concatenation(self, tmp_path: Path) -> None:
    source = tmp_path / "patterns.py"
    _ = source.write_text(_VALID_SOURCE, encoding="utf-8")
    regexes = PythonRegexImporter().load(source)
    patterns = {regex.name: regex.pattern for regex in regexes}
    assert patterns == {
      "_PATTERN_ONE": "foo",
      "_PATTERN_TWO": "barbaz",
      "_PATTERN_THREE": "foo-suffix",
    }

  def test_rejects_syntax_error(self, tmp_path: Path) -> None:
    source = tmp_path / "patterns.py"
    _ = source.write_text("def broken(:", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Python regex source"):
      _ = PythonRegexImporter().load(source)

  def test_rejects_missing_collection(self, tmp_path: Path) -> None:
    source = tmp_path / "patterns.py"
    _ = source.write_text('_PATTERN_ONE = "foo"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="PROHIBITED_PATTERNS"):
      _ = PythonRegexImporter().load(source)

  def test_rejects_empty_collection(self, tmp_path: Path) -> None:
    source = tmp_path / "patterns.py"
    _ = source.write_text(
      "PROHIBITED_PATTERNS = _compile_patterns()\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="at least one pattern"):
      _ = PythonRegexImporter().load(source)


class TestLoadViolations:
  def test_loads_json_violations(self, tmp_path: Path) -> None:
    path = tmp_path / "violations.json"
    _ = path.write_text(
      json.dumps(
        {
          "violations": [
            {"context": "please hack this", "value": "hack", "location": 0}
          ]
        }
      ),
      encoding="utf-8",
    )
    violations = load_violations(path)
    assert violations[0].value == "hack"

  def test_loads_text_violations(self, tmp_path: Path) -> None:
    path = tmp_path / "violations.txt"
    _ = path.write_text(
      'context: "please hack this" location: 0 value: "hack"', encoding="utf-8"
    )
    violations = load_violations(path)
    assert violations[0].value == "hack"

  def test_raises_for_unrecognized_format(self, tmp_path: Path) -> None:
    path = tmp_path / "violations.txt"
    _ = path.write_text("nothing useful here", encoding="utf-8")
    with pytest.raises(ValueError, match="context, location, and value"):
      _ = load_violations(path)


class TestBuildDiagnosisInput:
  def test_prefers_violation_label_over_default(self) -> None:
    violation = ImportedViolation(
      context="please hack this",
      source_location=0,
      value="hack",
      expected_outcome="allow",
      expected_reason="was a game reference",
    )
    data = build_diagnosis_input(
      name="test",
      regexes=(ImportedRegex(name="_HACK", pattern="hack"),),
      violation=violation,
      expected_outcome="block",
      expected_reason="default reason",
    )
    conversation = data["conversation"]
    assert isinstance(conversation, dict)
    assert conversation["expected_outcome"] == "allow"
    assert conversation["expected_outcome_source"] == "violation"

  def test_omits_expected_label_when_absent(self) -> None:
    violation = ImportedViolation(
      context="please hack this", source_location=0, value="hack"
    )
    data = build_diagnosis_input(name="test", regexes=(), violation=violation)
    conversation = data["conversation"]
    assert isinstance(conversation, dict)
    assert "expected_outcome" not in conversation


class TestImportedRegexManifest:
  def test_wraps_regexes_as_blocking_manifest(self) -> None:
    manifest = imported_regex_manifest(
      "test", (ImportedRegex(name="_HACK", pattern="hack"),)
    )
    assert manifest.rules[0].id == "hack"
    assert manifest.rules[0].action == "block"

  def test_build_manifest_diagnosis_input_uses_manifest_rules(self) -> None:
    manifest: RegexManifest = RegexManifest(
      name="test", rules=(RegexRule(id="hack", pattern="hack", action="block"),)
    )
    violation = ImportedViolation(
      context="please hack this", source_location=0, value="hack"
    )
    data = build_manifest_diagnosis_input(manifest=manifest, violation=violation)
    assert data["rules"] == [rule.to_dict() for rule in manifest.rules]
    assert data["match_timeout_seconds"] == manifest.match_timeout_seconds


class TestWriters:
  def test_write_regex_manifest_round_trips(self, tmp_path: Path) -> None:
    manifest = RegexManifest(
      name="test", rules=(RegexRule(id="hack", pattern="hack", action="block"),)
    )
    output = write_regex_manifest(tmp_path / "nested" / "manifest.yaml", manifest)
    loaded = cast("object", yaml.safe_load(output.read_text(encoding="utf-8")))
    assert isinstance(loaded, dict)
    assert loaded["name"] == "test"

  def test_write_diagnosis_input_round_trips(self, tmp_path: Path) -> None:
    output = write_diagnosis_input(
      tmp_path / "nested" / "diagnosis.yaml", {"name": "test"}
    )
    loaded = cast("object", yaml.safe_load(output.read_text(encoding="utf-8")))
    assert loaded == {"name": "test"}


class TestPythonRegexImporterSourceShapes:
  def _load(self, tmp_path: Path, source_text: str) -> tuple[ImportedRegex, ...]:
    source = tmp_path / "patterns.py"
    _ = source.write_text(source_text, encoding="utf-8")
    return PythonRegexImporter().load(source)

  def test_skips_statements_that_are_not_string_constants(self, tmp_path: Path) -> None:
    regexes = self._load(
      tmp_path,
      """\
import re

_COUNT = 3
_UNRESOLVED = missing_name
_BARE: str
_FIRST, _SECOND = "a", "b"
_PATTERN: str = "foo"
PROHIBITED_PATTERNS = _compile_patterns(_PATTERN)
""",
    )

    assert [(regex.name, regex.pattern) for regex in regexes] == [("_PATTERN", "foo")]

  @pytest.mark.parametrize(
    "definition",
    [
      "_BAD = 3",
      "_BAD = lookup()",
      '_BAD = _UNDEFINED + "suffix"',
      '_BAD = f"{_UNDEFINED}"',
      '_BAD = f"{_GOOD!r}"',
      '_BAD = f"{_GOOD:>5}"',
    ],
  )
  def test_rejects_collection_entries_that_cannot_be_resolved(
    self, tmp_path: Path, definition: str
  ) -> None:
    source = (
      f'_GOOD = "foo"\n{definition}\nPROHIBITED_PATTERNS = _compile_patterns(_BAD)\n'
    )

    with pytest.raises(ValueError, match="unresolved _BAD"):
      _ = self._load(tmp_path, source)

  @pytest.mark.parametrize(
    "collection",
    [
      "[]",
      "other_function(_PATTERN)",
      "_compile_patterns(_PATTERN, flags=0)",
      "module._compile_patterns(_PATTERN)",
    ],
  )
  def test_rejects_collections_that_are_not_direct_calls(
    self, tmp_path: Path, collection: str
  ) -> None:
    source = f'_PATTERN = "foo"\nPROHIBITED_PATTERNS = {collection}\n'

    with pytest.raises(ValueError, match="direct _compile_patterns"):
      _ = self._load(tmp_path, source)

  def test_rejects_literal_collection_arguments(self, tmp_path: Path) -> None:
    source = 'PROHIBITED_PATTERNS = _compile_patterns("foo")\n'

    with pytest.raises(TypeError, match="named regex constants"):
      _ = self._load(tmp_path, source)


class TestLoadViolationsFailures:
  @staticmethod
  def _violation(**overrides: object) -> str:
    violation: dict[str, object] = {
      "context": "please hack",
      "value": "hack",
      "location": 7,
    }
    violation.update(overrides)
    return json.dumps({"violations": [violation]})

  def test_rejects_a_json_document_that_is_not_an_object(self) -> None:
    with pytest.raises(TypeError, match="must contain an object"):
      _ = load_violations_text("[1]")

  @pytest.mark.parametrize("payload", ["{}", '{"violations": []}', '{"violations": 1}'])
  def test_rejects_a_missing_or_empty_violation_list(self, payload: str) -> None:
    with pytest.raises(ValueError, match="non-empty violations list"):
      _ = load_violations_text(payload)

  def test_rejects_an_entry_that_is_not_an_object(self) -> None:
    with pytest.raises(TypeError, match=r"violations\[0\] must be an object"):
      _ = load_violations_text('{"violations": ["text"]}')

  @pytest.mark.parametrize(
    "overrides,message",
    [
      ({"context": ""}, r"violations\[0\]\.context must be a non-empty string"),
      ({"context": 4}, r"violations\[0\]\.context must be a non-empty string"),
      ({"value": ""}, r"violations\[0\]\.value must be a non-empty string"),
      ({"location": -1}, r"location must be a non-negative integer"),
      ({"location": True}, r"location must be a non-negative integer"),
      ({"location": "7"}, r"location must be a non-negative integer"),
      (
        {"expected_outcome": "maybe", "expected_reason": "why"},
        r"expected_outcome must be 'allow' or 'block'",
      ),
      (
        {"expected_outcome": "allow", "expected_reason": " "},
        r"expected_reason must be a non-empty string",
      ),
      (
        {"expected_outcome": "allow", "expected_reason": 3},
        r"expected_reason must be a non-empty string",
      ),
      ({"expected_outcome": "allow"}, r"must be provided together"),
      ({"expected_reason": "why"}, r"must be provided together"),
    ],
  )
  def test_rejects_invalid_entry_fields(
    self, overrides: dict[str, object], message: str
  ) -> None:
    with pytest.raises(ValueError, match=message):
      _ = load_violations_text(self._violation(**overrides))

  def test_accepts_a_complete_expected_label(self) -> None:
    violation = load_violations_text(
      self._violation(expected_outcome="allow", expected_reason=" Neutral. ")
    )[0]

    assert violation.expected_outcome == "allow"
    assert violation.expected_reason == "Neutral."

  def test_rejects_an_empty_text_format_value(self) -> None:
    with pytest.raises(
      ValueError, match="violation context must be a non-empty string"
    ):
      _ = load_violations_text('context: "" location: 0 value: "x"')
