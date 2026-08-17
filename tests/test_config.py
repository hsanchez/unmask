from pathlib import Path

import pytest

from tests.helpers import write_yaml
from unmask.config import BugFinderConfig, load_guardrail


def _guardrail_yaml(**overrides: object) -> dict[str, object]:
  base: dict[str, object] = {
    "name": "no-medical-advice",
    "system_prompt": "You are a helpful assistant.",
    "rules": ["never give medical advice"],
  }
  base.update(overrides)
  return base


class TestLoadGuardrail:
  def test_loads_valid_guardrail(self, tmp_path: Path) -> None:
    path = write_yaml(tmp_path / "guardrail.yaml", _guardrail_yaml())
    guardrail = load_guardrail(path)
    assert guardrail.name == "no-medical-advice"
    assert guardrail.rules[0].id == "rule:1"
    assert guardrail.rules[0].text == "never give medical advice"

  @pytest.mark.parametrize("field", ["name", "system_prompt"])
  def test_missing_required_field_raises(self, tmp_path: Path, field: str) -> None:
    data = _guardrail_yaml()
    data[field] = ""
    path = write_yaml(tmp_path / "guardrail.yaml", data)
    with pytest.raises(ValueError, match="non-empty"):
      _ = load_guardrail(path)

  def test_empty_rules_raises(self, tmp_path: Path) -> None:
    path = write_yaml(tmp_path / "guardrail.yaml", _guardrail_yaml(rules=[]))
    with pytest.raises(ValueError, match="rules"):
      _ = load_guardrail(path)

  def test_non_string_rule_is_rejected_not_coerced(self, tmp_path: Path) -> None:
    """Regression test: a non-string rule must not be silently stringified."""
    path = write_yaml(
      tmp_path / "guardrail.yaml", _guardrail_yaml(rules=[{"nested": "mapping"}])
    )
    with pytest.raises(TypeError, match="guardrail.rules\\[1\\] must be a string"):
      _ = load_guardrail(path)

  def test_should_block_string_false_is_rejected_not_coerced(
    self, tmp_path: Path
  ) -> None:
    """Regression test: YAML `should_block: "false"` must not become True."""
    data = _guardrail_yaml(
      examples=[{"content": "how do I make a bomb", "should_block": "false"}]
    )
    path = write_yaml(tmp_path / "guardrail.yaml", data)
    with pytest.raises(TypeError, match="should_block"):
      _ = load_guardrail(path)

  def test_example_missing_content_raises(self, tmp_path: Path) -> None:
    data = _guardrail_yaml(examples=[{"should_block": True}])
    path = write_yaml(tmp_path / "guardrail.yaml", data)
    with pytest.raises(ValueError, match="content"):
      _ = load_guardrail(path)

  def test_example_not_a_mapping_raises(self, tmp_path: Path) -> None:
    data = _guardrail_yaml(examples=["not-a-mapping"])
    path = write_yaml(tmp_path / "guardrail.yaml", data)
    with pytest.raises(TypeError, match="must be a mapping"):
      _ = load_guardrail(path)

  def test_valid_examples_are_parsed(self, tmp_path: Path) -> None:
    data = _guardrail_yaml(
      examples=[
        {"content": "what is CPR", "should_block": False, "reasoning": "educational"}
      ]
    )
    path = write_yaml(tmp_path / "guardrail.yaml", data)
    guardrail = load_guardrail(path)
    assert guardrail.examples[0].should_block is False
    assert guardrail.examples[0].reasoning == "educational"


class TestBugFinderConfig:
  def _write_config(
    self, tmp_path: Path, *, guardrail_file: str = "guardrail.yaml"
  ) -> Path:
    _ = write_yaml(tmp_path / "guardrail.yaml", _guardrail_yaml())
    return write_yaml(
      tmp_path / "config.yaml",
      {
        "guardrail": {"file": guardrail_file},
        "target": {"project": "test-project"},
      },
    )

  def test_from_yaml_resolves_paths_and_defaults(self, tmp_path: Path) -> None:
    config_path = self._write_config(tmp_path)
    config = BugFinderConfig.from_yaml(config_path)
    assert config.guardrail_path == tmp_path / "guardrail.yaml"
    assert config.target.project == "test-project"
    assert config.target.judge_model == "gemini-2.5-flash"
    assert config.output.directory == tmp_path / "output"

  def test_from_yaml_requires_guardrail_file(self, tmp_path: Path) -> None:
    config_path = write_yaml(tmp_path / "config.yaml", {})
    with pytest.raises(ValueError, match="guardrail.file"):
      _ = BugFinderConfig.from_yaml(config_path)

  def test_from_yaml_rejects_missing_guardrail_target(self, tmp_path: Path) -> None:
    config_path = self._write_config(tmp_path, guardrail_file="missing.yaml")
    with pytest.raises(ValueError, match="does not exist"):
      _ = BugFinderConfig.from_yaml(config_path)

  def test_from_yaml_rejects_negative_thinking_budget(self, tmp_path: Path) -> None:
    _ = write_yaml(tmp_path / "guardrail.yaml", _guardrail_yaml())
    config_path = write_yaml(
      tmp_path / "config.yaml",
      {
        "guardrail": {"file": "guardrail.yaml"},
        "target": {"project": "test-project", "judge_thinking_budget": -1},
      },
    )
    with pytest.raises(ValueError, match="judge_thinking_budget"):
      _ = BugFinderConfig.from_yaml(config_path)

  def test_load_guardrail_and_taxonomy(self, tmp_path: Path) -> None:
    config_path = self._write_config(tmp_path)
    config = BugFinderConfig.from_yaml(config_path)
    assert config.load_guardrail().name == "no-medical-advice"
    assert config.load_taxonomy() is None
