"""YAML-backed configuration for guardrail loading and the bug-finder pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from .models import Guardrail, GuardrailExample, GuardrailRule


def _mapping(raw: object, name: str) -> dict[str, object]:
  if not isinstance(raw, dict):
    raise TypeError(f"{name} must be a mapping")
  return cast("dict[str, object]", raw)


def _as_list(raw: object) -> list[object] | None:
  return cast("list[object]", raw) if isinstance(raw, list) else None


def _resolve(base: Path, value: object) -> Path:
  path: Path = Path(str(value))
  return path if path.is_absolute() else base / path


def _require_rule_text(raw: object, index: int) -> str:
  if not isinstance(raw, str):
    raise TypeError(f"guardrail.rules[{index}] must be a string")
  return raw.strip()


def _parse_example(raw: object, index: int) -> GuardrailExample:
  """
  Raises:
    TypeError: if `raw` is not a mapping, or `should_block` is not a boolean.
    ValueError: if `content` is missing or empty.
  """
  entry: dict[str, object] = _mapping(raw, name=f"guardrail.examples[{index}]")
  content: str = str(entry.get("content", "")).strip()
  if not content:
    raise ValueError(f"guardrail.examples[{index}].content must be non-empty")

  should_block: object = entry.get("should_block")
  if not isinstance(should_block, bool):
    raise TypeError(f"guardrail.examples[{index}].should_block must be a boolean")

  return GuardrailExample(
    content=content,
    should_block=should_block,
    reasoning=str(entry.get("reasoning", "")).strip(),
  )


def load_guardrail(path: Path) -> Guardrail:
  """Load and validate a guardrail definition from a YAML file.

  Raises:
    TypeError: if the YAML document, or a rule or example within it, is malformed.
    ValueError: if `name`, `system_prompt`, or `rules` is missing or empty.
  """
  raw: dict[str, object] = _mapping(
    yaml.safe_load(path.read_text(encoding="utf-8")) or {}, name="guardrail"
  )
  name: str = str(raw.get("name", "")).strip()
  system_prompt: str = str(raw.get("system_prompt", "")).strip()
  if not name or not system_prompt:
    raise ValueError("guardrail name and system_prompt must be non-empty")

  raw_rules = _as_list(raw.get("rules"))
  if not raw_rules:
    raise ValueError("guardrail.rules must be a non-empty list")

  rules: tuple[GuardrailRule, ...] = tuple(
    GuardrailRule(id=f"rule:{index}", text=text)
    for index, raw_rule in enumerate(raw_rules, start=1)
    for text in [_require_rule_text(raw_rule, index)]
    if text
  )
  if not rules:
    raise ValueError("guardrail.rules must contain a non-empty rule")

  raw_examples = _as_list(raw.get("examples", [])) or []
  examples: tuple[GuardrailExample, ...] = tuple(
    _parse_example(raw=item, index=index)
    for index, item in enumerate(raw_examples, start=1)
  )

  return Guardrail(
    name=name, system_prompt=system_prompt, rules=rules, examples=examples
  )


@dataclass(frozen=True)
class TargetConfig:
  """The judge model and Vertex AI location used for semantic extraction."""

  project: str | None
  location: str
  judge_model: str
  thinking_budget: int | None


@dataclass(frozen=True)
class OutputConfig:
  """Where the bug-finder pipeline writes its symbolic model and findings."""

  directory: Path
  findings_file: str = "findings.json"
  symbolic_model_file: str = "symbolic_model.json"


@dataclass(frozen=True)
class BugFinderConfig:
  """The full configuration for one run of the neuro-symbolic bug-finder pipeline."""

  source: Path
  guardrail_path: Path
  taxonomy_path: Path | None
  symbolic_model_path: Path | None
  target: TargetConfig
  output: OutputConfig

  @classmethod
  def from_yaml(cls, path: str | Path) -> BugFinderConfig:
    """Load a pipeline config from YAML, resolving relative paths against its directory.

    Raises:
      TypeError: if the YAML document has the wrong shape for a required section.
      ValueError: if `guardrail.file` is missing, `judge_thinking_budget` is
        invalid, or a referenced file does not exist (see `validate_files`).
    """
    source: Path = Path(path).resolve()
    raw: dict[str, object] = _mapping(
      yaml.safe_load(source.read_text(encoding="utf-8")) or {}, name="config"
    )
    base: Path = source.parent

    guardrail: dict[str, object] = _mapping(raw.get("guardrail", {}), name="guardrail")
    if "file" not in guardrail:
      raise ValueError("guardrail.file is required")
    guardrail_path: Path = _resolve(base, value=guardrail["file"])

    taxonomy_raw = raw.get("taxonomy", {})
    taxonomy: dict[str, object] = (
      _mapping(taxonomy_raw, name="taxonomy") if taxonomy_raw else {}
    )
    taxonomy_path: Path | None = (
      _resolve(base, value=taxonomy["file"]) if "file" in taxonomy else None
    )

    analyzer: dict[str, object] = _mapping(raw.get("analyzer", {}), name="analyzer")
    symbolic_model_path: Path | None = (
      _resolve(base, value=analyzer["symbolic_model"])
      if "symbolic_model" in analyzer
      else None
    )

    target: dict[str, object] = _mapping(raw.get("target", {}), name="target")
    model: str = str(target.get("model", "gemini-2.5-flash"))
    raw_project = target.get("project")
    project: str | None = (
      str(raw_project) if raw_project else os.getenv("GOOGLE_CLOUD_PROJECT")
    )
    raw_thinking_budget = target.get("judge_thinking_budget", 128)
    thinking_budget: int | None
    if raw_thinking_budget is None:
      thinking_budget = None
    elif isinstance(raw_thinking_budget, int) and raw_thinking_budget >= 0:
      thinking_budget = raw_thinking_budget
    else:
      raise ValueError("target.judge_thinking_budget must be a non-negative integer")

    output: dict[str, object] = _mapping(raw.get("output", {}), name="output")
    output_directory: Path = _resolve(base, value=output.get("directory", "output"))

    config = cls(
      source=source,
      guardrail_path=guardrail_path,
      taxonomy_path=taxonomy_path,
      symbolic_model_path=symbolic_model_path,
      target=TargetConfig(
        project=str(project) if project else None,
        location=str(
          target.get("location", os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
        ),
        judge_model=str(target.get("judge_model", model)),
        thinking_budget=thinking_budget,
      ),
      output=OutputConfig(
        directory=output_directory,
        findings_file=str(output.get("findings_file", "findings.json")),
        symbolic_model_file=str(
          output.get("symbolic_model_file", "symbolic_model.json")
        ),
      ),
    )
    config.validate_files()
    return config

  def validate_files(self) -> None:
    """
    Raises:
      ValueError: if `guardrail_path`, `taxonomy_path`, or `symbolic_model_path`
        is set but does not point to an existing file.
    """
    for name, path in [
      ("guardrail.file", self.guardrail_path),
      ("taxonomy.file", self.taxonomy_path),
      ("analyzer.symbolic_model", self.symbolic_model_path),
    ]:
      if path is not None and not path.is_file():
        raise ValueError(f"{name} does not exist: '{path}'")

  def load_guardrail(self) -> Guardrail:
    return load_guardrail(path=self.guardrail_path)

  def load_taxonomy(self) -> object | None:
    if self.taxonomy_path is None:
      return None
    return cast(
      "object", yaml.safe_load(self.taxonomy_path.read_text(encoding="utf-8"))
    )
