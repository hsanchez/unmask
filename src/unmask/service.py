"""In-memory validation, scan, and diagnosis services shared by CLI and web callers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

import yaml
from yaml.error import Mark
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .analyzer import SymbolicAnalyzer
from .backend import JudgeBackend, create_backend
from .config import TargetConfig, load_guardrail_text
from .diagnosis import (
  ConversationIncident,
  DiagnosisConfig,
  DiagnosisFinding,
  DiagnosisReport,
  RegexDiagnoser,
  RegexManifest,
  TextLocation,
)
from .extractor import SemanticExtractor
from .importer import ImportedViolation, load_violations_text
from .models import Finding, Guardrail, SymbolicModel
from .scanning import RegexScanReport, scan_regex_manifest

type SpecificationKind = Literal["natural_language", "regex"]
type AnalysisWorkflow = Literal["scan", "diagnose"]

DEFAULT_MODELS: dict[str, str] = {
  "gemini": "gemini-2.5-flash",
  "openai": "gpt-4o",
  "anthropic": "claude-opus-4-5",
}

_compose_yaml = cast("Callable[[str], object]", yaml.compose)


class BackendFactory(Protocol):
  def __call__(self, config: TargetConfig) -> JudgeBackend: ...


@dataclass(frozen=True)
class StructuralUnit:
  """One source region that can be associated with analysis findings."""

  id: str
  kind: str
  rule_id: str
  start: int
  end: int

  def to_dict(self) -> dict[str, object]:
    return {
      "id": self.id,
      "kind": self.kind,
      "ruleId": self.rule_id,
      "start": self.start,
      "end": self.end,
    }


@dataclass(frozen=True)
class SourceDocument:
  """A verbatim source document and its addressable structural units."""

  id: str
  role: str
  name: str
  raw_text: str
  structural_units: tuple[StructuralUnit, ...]
  media_type: str = "application/yaml"

  def to_dict(self) -> dict[str, object]:
    return {
      "id": self.id,
      "role": self.role,
      "name": self.name,
      "mediaType": self.media_type,
      "rawText": self.raw_text,
      "structuralUnits": [item.to_dict() for item in self.structural_units],
    }


@dataclass(frozen=True)
class SourceReference:
  """A half-open source range associated with a finding."""

  document_id: str
  structural_unit_id: str
  start: int
  end: int

  def to_dict(self) -> dict[str, object]:
    return {
      "documentId": self.document_id,
      "structuralUnitId": self.structural_unit_id,
      "start": self.start,
      "end": self.end,
    }


@dataclass(frozen=True)
class UiFinding:
  """A finding normalized for bidirectional source navigation."""

  id: str
  bug_type: str
  severity: str
  summary: str
  rationale: str
  rule_ids: tuple[str, ...]
  source_references: tuple[SourceReference, ...]
  witness: dict[str, bool]
  metadata: dict[str, object]

  def to_dict(self) -> dict[str, object]:
    return {
      "id": self.id,
      "type": self.bug_type,
      "severity": self.severity,
      "summary": self.summary,
      "rationale": self.rationale,
      "ruleIds": list(self.rule_ids),
      "sourceReferences": [item.to_dict() for item in self.source_references],
      "witness": dict(sorted(self.witness.items())),
      "metadata": self.metadata,
    }


@dataclass(frozen=True)
class ValidationResult:
  """A validated specification and the source units addressable by the UI."""

  specification_kind: SpecificationKind
  name: str
  document: SourceDocument

  def to_dict(self) -> dict[str, object]:
    return {
      "schemaVersion": 1,
      "specificationKind": self.specification_kind,
      "name": self.name,
      "document": self.document.to_dict(),
    }


@dataclass(frozen=True)
class ScanResult:
  """A unified UI result plus the existing CLI artifact payloads."""

  specification_kind: SpecificationKind
  name: str
  content_hash: str
  document: SourceDocument
  summary: dict[str, object]
  findings: tuple[UiFinding, ...]
  workflow: AnalysisWorkflow = "scan"
  symbolic_model: SymbolicModel | None = None
  regex_report: RegexScanReport | None = None
  diagnosis_reports: tuple[DiagnosisReport, ...] | None = None

  def to_dict(self) -> dict[str, object]:
    result: dict[str, object] = {
      "schemaVersion": 1,
      "status": "completed",
      "workflow": self.workflow,
      "specificationKind": self.specification_kind,
      "name": self.name,
      "contentHash": self.content_hash,
      "documents": [self.document.to_dict()],
      "summary": self.summary,
      "findings": [item.to_dict() for item in self.findings],
      "warnings": [],
      "errors": [],
    }
    if self.symbolic_model is not None:
      result["symbolicModel"] = self.symbolic_model.to_dict()
    return result


def _mapping_value(node: MappingNode, key: str) -> Node | None:
  entries = cast("list[tuple[Node, Node]]", node.value)
  for raw_key, value in entries:
    if isinstance(raw_key, ScalarNode) and _scalar_value(raw_key) == key:
      return value
  return None


def _scalar_value(node: ScalarNode) -> str:
  value = cast("object", node.value)
  if not isinstance(value, str):
    raise TypeError("YAML scalar node must contain text")
  return value


def _sequence_values(node: SequenceNode) -> list[Node]:
  return cast("list[Node]", node.value)


def _node_offsets(node: Node) -> tuple[int, int]:
  start_mark = cast("Mark | None", node.start_mark)
  end_mark = cast("Mark | None", node.end_mark)
  if start_mark is None or end_mark is None:
    raise TypeError("composed YAML node must have source marks")
  return start_mark.index, end_mark.index


def _composed_mapping(text: str) -> MappingNode:
  root = _compose_yaml(text)
  if not isinstance(root, MappingNode):
    raise TypeError("specification must be a YAML mapping")
  return root


def _natural_units(root: MappingNode) -> tuple[StructuralUnit, ...]:
  rules_node: Node | None = _mapping_value(root, "rules")
  if not isinstance(rules_node, SequenceNode):
    return ()
  units: list[StructuralUnit] = []
  for index, node in enumerate(_sequence_values(rules_node), start=1):
    start, end = _node_offsets(node)
    units.append(
      StructuralUnit(
        id=f"specification:rule:{index}",
        kind="rule",
        rule_id=f"rule:{index}",
        start=start,
        end=end,
      )
    )
  return tuple(units)


def _regex_units(root: MappingNode) -> tuple[StructuralUnit, ...]:
  rules_node: Node | None = _mapping_value(root, "rules")
  if not isinstance(rules_node, SequenceNode):
    return ()
  units: list[StructuralUnit] = []
  for index, node in enumerate(_sequence_values(rules_node), start=1):
    if not isinstance(node, MappingNode):
      continue
    id_node: Node | None = _mapping_value(node, "id")
    if not isinstance(id_node, ScalarNode):
      continue
    start, end = _node_offsets(node)
    units.append(
      StructuralUnit(
        id=f"specification:rule:{index}",
        kind="rule",
        rule_id=_scalar_value(id_node),
        start=start,
        end=end,
      )
    )
  return tuple(units)


def classify_specification(text: str) -> SpecificationKind:
  """Classify YAML text as a supported specification kind.

  Raises:
    TypeError: if the document is not a mapping.
    ValueError: if the document is not a supported specification.
    yaml.YAMLError: if `text` is malformed YAML.
  """
  loaded: object = cast("object", yaml.safe_load(text))
  if not isinstance(loaded, dict):
    raise TypeError("specification must be a YAML mapping")
  raw: dict[object, object] = cast("dict[object, object]", loaded)
  kind: object | None = raw.get("kind")
  if kind == "regex":
    return "regex"
  if kind is not None:
    raise ValueError(f"unsupported specification kind: {kind!r}")
  if all(field in raw for field in ("name", "system_prompt", "rules")):
    return "natural_language"
  raise ValueError(
    "specification must be a regex manifest with kind: regex or a "
    + "natural-language guardrail with name, system_prompt, and rules"
  )


def validate_specification(
  text: str,
  *,
  name: str = "specification.yaml",
) -> ValidationResult:
  """Validate specification YAML and preserve its rule source ranges.

  Raises:
    TypeError: if the specification has the wrong shape.
    ValueError: if the specification has invalid values.
    yaml.YAMLError: if the specification is malformed YAML.
  """
  kind: SpecificationKind = classify_specification(text)
  root: MappingNode = _composed_mapping(text)
  if kind == "natural_language":
    guardrail: Guardrail = load_guardrail_text(text)
    specification_name: str = guardrail.name
    units: tuple[StructuralUnit, ...] = _natural_units(root)
  else:
    manifest: RegexManifest = RegexManifest.from_text(text)
    specification_name = manifest.name
    units = _regex_units(root)
  return ValidationResult(
    specification_kind=kind,
    name=specification_name,
    document=SourceDocument(
      id="specification",
      role="specification",
      name=name,
      raw_text=text,
      structural_units=units,
    ),
  )


def _references(
  rule_ids: tuple[str, ...], document: SourceDocument
) -> tuple[SourceReference, ...]:
  units: dict[str, StructuralUnit] = {
    unit.rule_id: unit for unit in document.structural_units
  }
  return tuple(
    SourceReference(
      document_id=document.id,
      structural_unit_id=units[rule_id].id,
      start=units[rule_id].start,
      end=units[rule_id].end,
    )
    for rule_id in rule_ids
    if rule_id in units
  )


def _natural_finding(finding: Finding, document: SourceDocument) -> UiFinding:
  return UiFinding(
    id=finding.id,
    bug_type=finding.bug_type,
    severity=finding.severity,
    summary=finding.summary,
    rationale=finding.rationale,
    rule_ids=finding.rule_ids,
    source_references=_references(finding.rule_ids, document),
    witness=finding.witness,
    metadata=finding.metadata,
  )


def _regex_finding(
  finding: DiagnosisFinding,
  *,
  case_index: int,
  example_content: str,
  document: SourceDocument,
) -> UiFinding:
  return UiFinding(
    id=f"case:{case_index}:{finding.id}",
    bug_type=finding.bug_type,
    severity=finding.severity,
    summary=finding.summary,
    rationale=finding.explanation,
    rule_ids=finding.rule_ids,
    source_references=_references(finding.rule_ids, document),
    witness=finding.proof.witness,
    metadata={
      **finding.metadata,
      "caseIndex": case_index,
      "exampleContent": example_content,
      "remediation": finding.remediation,
      "proof": finding.proof.to_dict(),
      "evidence": [item.to_dict() for item in finding.evidence],
    },
  )


def _stable_hash(payload: dict[str, object]) -> str:
  encoded: str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
  return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def scan_specification(
  specification_text: str,
  *,
  specification_name: str = "specification.yaml",
  taxonomy_text: str | None = None,
  target: TargetConfig,
  backend_factory: BackendFactory = create_backend,
) -> ScanResult:
  """Run a supported specification scan entirely from in-memory text.

  Raises:
    RuntimeError: if a configured model provider cannot execute.
    TypeError: if an input document has the wrong shape.
    ValueError: if an input document or extracted model is invalid.
    yaml.YAMLError: if an input is malformed YAML.
  """
  validation: ValidationResult = validate_specification(
    specification_text, name=specification_name
  )
  content_hash: str = _stable_hash(
    {
      "schemaVersion": 1,
      "specification": specification_text,
      "taxonomy": taxonomy_text,
      "provider": target.provider,
      "model": target.judge_model,
      "thinkingBudget": target.thinking_budget,
    }
  )
  if validation.specification_kind == "natural_language":
    guardrail: Guardrail = load_guardrail_text(specification_text)
    taxonomy: object | None = (
      cast("object", yaml.safe_load(taxonomy_text))
      if taxonomy_text is not None
      else None
    )
    model: SymbolicModel = SemanticExtractor(backend_factory(target)).extract(
      guardrail, taxonomy
    )
    findings: list[Finding] = SymbolicAnalyzer(model, guardrail).analyze()
    counts: dict[str, int] = {
      bug_type: sum(finding.bug_type == bug_type for finding in findings)
      for bug_type in ("over_strict", "contradiction", "ambiguity")
    }
    return ScanResult(
      specification_kind="natural_language",
      name=guardrail.name,
      content_hash=content_hash,
      document=validation.document,
      summary={
        "status": "supported_defect" if findings else "no_supported_defect",
        "rules": len(guardrail.rules),
        "findings": len(findings),
        **counts,
      },
      findings=tuple(
        _natural_finding(finding, validation.document) for finding in findings
      ),
      symbolic_model=model,
    )

  manifest: RegexManifest = RegexManifest.from_text(specification_text)
  report: RegexScanReport = scan_regex_manifest(manifest, taxonomy_text=taxonomy_text)
  summary = cast("dict[str, object]", report.to_dict()["summary"])
  regex_findings: tuple[UiFinding, ...] = tuple(
    _regex_finding(
      finding,
      case_index=case.index,
      example_content=case.example.content,
      document=validation.document,
    )
    for case in report.cases
    for finding in case.diagnosis.findings
  )
  return ScanResult(
    specification_kind="regex",
    name=manifest.name,
    content_hash=content_hash,
    document=validation.document,
    summary=summary,
    findings=regex_findings,
    regex_report=report,
  )


def incident_from_violation(violation: ImportedViolation) -> ConversationIncident:
  """Build the conversation incident used by CLI and studio diagnosis.

  Raises:
    ValueError: if the violation value is missing or ambiguous in its context.
  """
  start: int = violation.local_start
  return ConversationIncident(
    text=violation.context,
    value=violation.value,
    location=TextLocation(start=start, end=start + len(violation.value)),
    source_location=violation.source_location,
    observed_outcome="block",
    expected_outcome=violation.expected_outcome,
    expected_reason=violation.expected_reason,
    expected_outcome_source=(
      "violation" if violation.expected_outcome is not None else "missing"
    ),
  )


def diagnosis_status(report: DiagnosisReport) -> str:
  """Return the CLI/studio status for one incident report."""
  if report.findings:
    return "supported_defect"
  if report.matches:
    return "no_supported_defect"
  return "no_matching_rule"


def diagnose_specification(
  specification_text: str,
  violations_text: str,
  *,
  specification_name: str = "specification.yaml",
) -> ScanResult:
  """Diagnose reported incidents against an in-memory regex specification.

  Raises:
    TypeError: if an input document has the wrong shape.
    ValueError: if the specification is not regex or an input is invalid.
    yaml.YAMLError: if the specification is malformed YAML.
  """
  validation: ValidationResult = validate_specification(
    specification_text, name=specification_name
  )
  if validation.specification_kind != "regex":
    raise ValueError("diagnosis requires a regex specification")
  violations: tuple[ImportedViolation, ...] = load_violations_text(violations_text)
  manifest: RegexManifest = RegexManifest.from_text(specification_text)
  output_directory: Path = Path("<memory>")
  configs: tuple[DiagnosisConfig, ...] = tuple(
    DiagnosisConfig(
      source=Path(specification_name),
      name=f"{manifest.name} incident {index}",
      incident=incident_from_violation(violation),
      rules=manifest.rules,
      output_path=output_directory / f"{index:04d}.json",
      match_timeout_seconds=manifest.match_timeout_seconds,
    )
    for index, violation in enumerate(violations, start=1)
  )
  reports: tuple[DiagnosisReport, ...] = RegexDiagnoser().diagnose_many(configs)
  findings: tuple[UiFinding, ...] = tuple(
    _regex_finding(
      finding,
      case_index=index,
      example_content=report.incident.text,
      document=validation.document,
    )
    for index, report in enumerate(reports, start=1)
    for finding in report.findings
  )
  content_hash: str = _stable_hash(
    {
      "schemaVersion": 1,
      "workflow": "diagnose",
      "specification": specification_text,
      "violations": violations_text,
    }
  )
  statuses: list[str] = [diagnosis_status(report) for report in reports]
  if "supported_defect" in statuses:
    status = "supported_defect"
  elif "no_supported_defect" in statuses:
    status = "no_supported_defect"
  else:
    status = "no_matching_rule"
  return ScanResult(
    specification_kind="regex",
    name=manifest.name,
    content_hash=content_hash,
    document=validation.document,
    summary={
      "status": status,
      "workflow": "diagnose",
      "incidents": len(reports),
      "findings": len(findings),
      "limitedIncidents": sum(bool(report.skipped_analyses) for report in reports),
    },
    findings=findings,
    workflow="diagnose",
    diagnosis_reports=reports,
  )
