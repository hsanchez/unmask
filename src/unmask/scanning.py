"""Proactive scanning workflows for executable regex guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from .diagnosis import (
  ConversationIncident,
  DiagnosisConfig,
  DiagnosisReport,
  RegexDiagnoser,
  RegexExample,
  RegexManifest,
  TextLocation,
)


@dataclass(frozen=True)
class RegexScanCase:
  """One labeled example diagnosed against a regex manifest during a scan."""

  index: int
  example: RegexExample
  diagnosis: DiagnosisReport

  def to_dict(self) -> dict[str, object]:
    return {
      "index": self.index,
      "content": self.example.content,
      "expectedAction": self.example.expected_action,
      "reason": self.example.reason,
      "source": self.example.source,
      "diagnosis": self.diagnosis.to_dict(),
    }


@dataclass(frozen=True)
class RegexScanReport:
  """The result of diagnosing every labeled example in a regex manifest."""

  manifest: RegexManifest
  cases: tuple[RegexScanCase, ...]
  taxonomy_supplied: bool

  def to_dict(self) -> dict[str, object]:
    finding_count: int = sum(len(case.diagnosis.findings) for case in self.cases)
    if finding_count:
      status = "supported_defect"
    elif self.cases:
      status = "no_supported_defect"
    else:
      status = "insufficient_evidence"
    return {
      "schemaVersion": 1,
      "mode": "scan",
      "inputKind": "regex",
      "name": self.manifest.name,
      "summary": {
        "status": status,
        "rules": len(self.manifest.rules),
        "examples": len(self.cases),
        "manifestExamples": sum(
          case.example.source == "manifest" for case in self.cases
        ),
        "taxonomyExamples": sum(
          case.example.source == "taxonomy" for case in self.cases
        ),
        "findings": finding_count,
        "taxonomySupplied": self.taxonomy_supplied,
      },
      "unsupportedAnalyses": (
        []
        if self.cases
        else [
          {
            "reason": (
              "Regex validation succeeded, but trusted labeler examples "
              "are required for policy-aware defect proofs."
            )
          }
        ]
      ),
      "cases": [case.to_dict() for case in self.cases],
    }


def load_taxonomy_regex_examples(path: Path | None) -> tuple[RegexExample, ...]:
  """Load `regex_examples` from a taxonomy YAML file, or `()` if `path` is None.

  Raises:
    TypeError: if the taxonomy or an example entry is not a mapping.
    ValueError: if `regex_examples_version` is not `1`.
  """
  if path is None:
    return ()
  loaded_value: object = cast(
    "object", yaml.safe_load(stream=path.read_text(encoding="utf-8"))
  )
  loaded: object = loaded_value or {}
  if not isinstance(loaded, dict):
    raise TypeError("taxonomy must be a mapping")
  raw: dict[str, object] = cast("dict[str, object]", loaded)
  raw_examples_value: object | None = raw.get("regex_examples")
  if raw_examples_value is None:
    return ()
  version: object | None = raw.get("regex_examples_version")
  if not isinstance(version, int) or isinstance(version, bool) or version != 1:
    raise ValueError("taxonomy.regex_examples_version must be 1")
  if not isinstance(raw_examples_value, list):
    raise TypeError("taxonomy.regex_examples must be a list")
  raw_examples: list[object] = cast("list[object]", raw_examples_value)
  examples: list[RegexExample] = []
  for index, item in enumerate(raw_examples):
    if not isinstance(item, dict):
      raise TypeError(f"taxonomy.regex_examples[{index}] must be a mapping")
    normalized: dict[str, object] = {
      **cast("dict[str, object]", item),
      "source": "taxonomy",
    }
    examples.append(RegexExample.from_dict(raw=normalized, index=index))
  return tuple(examples)


def merge_regex_examples(
  manifest_examples: tuple[RegexExample, ...],
  taxonomy_examples: tuple[RegexExample, ...],
) -> tuple[RegexExample, ...]:
  """Merge manifest and taxonomy examples, preferring the manifest on content overlap.

  Raises:
    ValueError: if a taxonomy example shares content with a manifest example
      but disagrees on `expected_action`.
  """
  merged: list[RegexExample] = list(manifest_examples)
  by_content: dict[str, RegexExample] = {
    example.content: example for example in manifest_examples
  }
  for example in taxonomy_examples:
    existing: RegexExample | None = by_content.get(example.content)
    if existing is not None:
      if existing.expected_action != example.expected_action:
        raise ValueError(
          f"conflicting expected actions for regex example '{example.content}'"
        )
      continue
    merged.append(example)
    by_content[example.content] = example
  return tuple(merged)


def scan_regex_manifest(
  manifest: RegexManifest,
  taxonomy_path: Path | None = None,
) -> RegexScanReport:
  """Diagnose every labeled example bundled with `manifest` (and `taxonomy_path`, if given)."""
  examples: tuple[RegexExample, ...] = merge_regex_examples(
    manifest_examples=manifest.examples,
    taxonomy_examples=load_taxonomy_regex_examples(path=taxonomy_path),
  )
  configs: list[DiagnosisConfig] = []
  for index, example in enumerate(examples, start=1):
    incident: ConversationIncident = ConversationIncident(
      text=example.content,
      value=example.content,
      location=TextLocation(start=0, end=len(example.content)),
      expected_outcome=example.expected_action,
      expected_reason=example.reason,
      expected_outcome_source=example.source,
    )
    configs.append(
      DiagnosisConfig(
        source=Path("regex-scan"),
        name=f"{manifest.name} example {index}",
        incident=incident,
        rules=manifest.rules,
        output_path=Path("scan.json"),
        match_timeout_seconds=manifest.match_timeout_seconds,
      )
    )
  diagnoses: tuple[DiagnosisReport, ...] = (
    tuple(RegexDiagnoser().diagnose_many(configs=tuple(configs))) if configs else ()
  )
  cases: tuple[RegexScanCase, ...] = tuple(
    RegexScanCase(
      index=index,
      example=example,
      diagnosis=diagnosis,
    )
    for index, (example, diagnosis) in enumerate(
      zip(examples, diagnoses, strict=True),
      start=1,
    )
  )

  return RegexScanReport(
    manifest=manifest,
    cases=cases,
    taxonomy_supplied=taxonomy_path is not None,
  )
