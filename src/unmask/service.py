"""In-memory validation, scan, and diagnosis services shared by CLI and web callers."""

from __future__ import annotations

import itertools
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

import yaml
from yaml.error import Mark
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .analyzer import SymbolicAnalyzer
from .backend import JudgeBackend, JudgeResponse, create_backend
from .config import TargetConfig, guardrail_from_data, load_guardrail_text
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
from .models import Finding, Guardrail, SymbolicModel, canonical_json_hash
from .models import SourceDocument as SourceDocument
from .models import StructuralUnit as StructuralUnit
from .repair import (
  CandidateEdit,
  FindingFingerprint,
  PolicyExampleEvaluator,
  ProposedFix,
  ProposedFixStatus,
  RepairCandidateGenerator,
  RepairGenerationError,
  TrustedPolicyExample,
  UnavailableReason,
  ValidationCheck,
  ValidationStatus,
  apply_candidate_edit,
  content_hash,
  merge_policy_examples,
  parse_taxonomy_policy_examples,
  unified_specification_diff,
)
from .scanning import RegexScanCase, RegexScanReport, scan_regex_manifest

type SpecificationKind = Literal["natural_language", "regex"]
type AnalysisWorkflow = Literal["scan", "diagnose"]

PROVIDER_MODELS: dict[str, tuple[str, ...]] = {
  "gemini": (
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
  ),
  "openai": (
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
  ),
  "anthropic": (
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
  ),
}
DEFAULT_MODELS: dict[str, str] = {
  provider: models[0] for provider, models in PROVIDER_MODELS.items()
}

logger = logging.getLogger(__name__)

_compose_yaml = cast("Callable[[str], object]", yaml.compose)


class BackendFactory(Protocol):
  def __call__(self, config: TargetConfig) -> JudgeBackend: ...


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
class BugFinding:
  """A detected defect with its rationale, witness, and source references."""

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
  specification: Guardrail | RegexManifest

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
  findings: tuple[BugFinding, ...]
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
  return _specification_kind(cast("object", yaml.safe_load(text)))


def _specification_kind(loaded: object) -> SpecificationKind:
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
  loaded: object = cast("object", yaml.safe_load(text))
  kind: SpecificationKind = _specification_kind(loaded)
  root: MappingNode = _composed_mapping(text)
  if kind == "natural_language":
    specification: Guardrail | RegexManifest = guardrail_from_data(loaded)
    units: tuple[StructuralUnit, ...] = _natural_units(root)
  else:
    specification = RegexManifest.from_dict(loaded)
    units = _regex_units(root)
  return ValidationResult(
    specification_kind=kind,
    name=specification.name,
    specification=specification,
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


def _natural_finding(finding: Finding, document: SourceDocument) -> BugFinding:
  return BugFinding(
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
) -> BugFinding:
  return BugFinding(
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
      "counterexampleExplanation": finding.counterexample_explanation.to_dict(),
      "proof": finding.proof.to_dict(),
      "evidence": [item.to_dict() for item in finding.evidence],
    },
  )


def bugs(
  specification_text: str,
  *,
  specification_name: str = "specification.yaml",
  taxonomy_text: str | None = None,
  model: TargetConfig | None = None,
  backend_factory: BackendFactory = create_backend,
) -> ScanResult:
  """Find specification defects; only natural-language analysis needs a model.

  Raises:
    RuntimeError: if a configured model provider cannot execute.
    TypeError: if an input document has the wrong shape.
    ValueError: if an input document or extracted model is invalid.
    yaml.YAMLError: if an input is malformed YAML.
  """
  if model is not None and not isinstance(cast("object", model), TargetConfig):
    raise TypeError("model must be a TargetConfig")
  validation: ValidationResult = validate_specification(
    specification_text, name=specification_name
  )
  binding: dict[str, object] = {
    "schemaVersion": 1,
    "specification": specification_text,
    "taxonomy": taxonomy_text,
  }
  if isinstance(validation.specification, Guardrail):
    if model is None:
      raise ValueError("natural-language analysis requires model configuration")
    binding.update(
      {
        "provider": model.provider,
        "model": model.judge_model,
        "thinkingBudget": model.thinking_budget,
      }
    )
    guardrail = validation.specification
    taxonomy: object | None = (
      cast("object", yaml.safe_load(taxonomy_text))
      if taxonomy_text is not None
      else None
    )
    symbolic_model: SymbolicModel = SemanticExtractor(backend_factory(model)).extract(
      guardrail, taxonomy
    )
    findings: list[Finding] = SymbolicAnalyzer(symbolic_model, guardrail).analyze()
    counts: dict[str, int] = {
      bug_type: sum(finding.bug_type == bug_type for finding in findings)
      for bug_type in ("over_strict", "contradiction", "ambiguity")
    }
    return ScanResult(
      specification_kind="natural_language",
      name=guardrail.name,
      content_hash=canonical_json_hash(binding),
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
      symbolic_model=symbolic_model,
    )

  manifest = validation.specification
  report: RegexScanReport = scan_regex_manifest(manifest, taxonomy_text=taxonomy_text)
  summary = cast("dict[str, object]", report.to_dict()["summary"])
  regex_findings: tuple[BugFinding, ...] = tuple(
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
    content_hash=canonical_json_hash(binding),
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
  source: Path | None = None,
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
  if not isinstance(validation.specification, RegexManifest):
    raise ValueError("diagnosis requires a regex specification")
  violations: tuple[ImportedViolation, ...] = load_violations_text(violations_text)
  manifest = validation.specification
  output_directory: Path = Path("<memory>")
  configs: tuple[DiagnosisConfig, ...] = tuple(
    DiagnosisConfig(
      source=source if source is not None else Path(specification_name),
      name=f"{manifest.name} incident {index}",
      incident=incident_from_violation(violation),
      rules=manifest.rules,
      output_path=output_directory / f"{index:04d}.json",
      match_timeout_seconds=manifest.match_timeout_seconds,
    )
    for index, violation in enumerate(violations, start=1)
  )
  reports: tuple[DiagnosisReport, ...] = RegexDiagnoser().diagnose_many(configs)
  findings: tuple[BugFinding, ...] = tuple(
    _regex_finding(
      finding,
      case_index=index,
      example_content=report.incident.text,
      document=validation.document,
    )
    for index, report in enumerate(reports, start=1)
    for finding in report.findings
  )
  content_hash: str = canonical_json_hash(
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


def _finding_fingerprint(
  finding: BugFinding,
  *,
  specification_kind: SpecificationKind,
  workflow: AnalysisWorkflow,
) -> FindingFingerprint:
  example: object | None = finding.metadata.get("exampleContent")
  return FindingFingerprint(
    specification_kind=specification_kind,
    workflow=workflow,
    bug_type=finding.bug_type,
    rule_ids=tuple(sorted(finding.rule_ids)),
    finding_id=finding.id,
    case_index=cast("int | None", finding.metadata.get("caseIndex")),
    example_content_hash=(content_hash(example) if isinstance(example, str) else None),
  )


def _finding_signatures(
  result: ScanResult,
) -> set[tuple[str, tuple[str, ...], str | None]]:
  return {
    _finding_fingerprint(
      finding,
      specification_kind=result.specification_kind,
      workflow=result.workflow,
    ).signature()
    for finding in result.findings
  }


def _unavailable_fix(
  *,
  fingerprint: FindingFingerprint,
  original_content_hash: str,
  target: TargetConfig,
  checks: list[ValidationCheck],
  reasons: tuple[UnavailableReason, ...],
  explanation: str | None = None,
) -> ProposedFix:
  return ProposedFix(
    status=ProposedFixStatus.UNAVAILABLE,
    specification_kind=fingerprint.specification_kind,
    finding_fingerprint=fingerprint,
    original_content_hash=original_content_hash,
    provider=target.provider,
    model=target.judge_model,
    validation_checks=tuple(checks),
    unavailable_reasons=reasons,
    explanation=explanation,
  )


def _affected_rules(
  specification_text: str,
  *,
  kind: SpecificationKind,
  rule_ids: tuple[str, ...],
) -> tuple[dict[str, object], list[dict[str, object]]]:
  if kind == "natural_language":
    guardrail: Guardrail = load_guardrail_text(specification_text)
    rules: dict[str, object] = {
      rule.id: rule.text for rule in guardrail.rules if rule.id in rule_ids
    }
    examples: list[dict[str, object]] = [
      {
        "content": example.content,
        "should_block": example.should_block,
        "reasoning": example.reasoning,
      }
      for example in guardrail.examples
    ]
    return rules, examples
  manifest: RegexManifest = RegexManifest.from_text(specification_text)
  regex_rules: dict[str, object] = {
    rule.id: rule.to_dict() for rule in manifest.rules if rule.id in rule_ids
  }
  regex_examples: list[dict[str, object]] = [
    cast("dict[str, object]", example.to_dict()) for example in manifest.examples
  ]
  return regex_rules, regex_examples


def _expected_outcome_passes(expected: str, actions: set[str]) -> bool:
  opposite: str = "allow" if expected == "block" else "block"
  expected_is_supported: bool = expected == "allow" or expected in actions
  return expected_is_supported and opposite not in actions


def _regex_case_passes(case: RegexScanCase) -> bool:
  actions: set[str] = {match.action for match in case.diagnosis.matches}
  return _expected_outcome_passes(case.example.expected_action, actions)


def regex_examples_retained(
  baseline: RegexScanReport,
  revised: RegexScanReport,
  *,
  target_example_hash: str | None,
) -> tuple[bool, int]:
  required_cases = [
    (index, case)
    for index, case in enumerate(baseline.cases)
    if _regex_case_passes(case)
    or content_hash(case.example.content) == target_example_hash
  ]
  retained: bool = all(
    index < len(revised.cases)
    and revised.cases[index].example == case.example
    and _regex_case_passes(revised.cases[index])
    for index, case in required_cases
  )
  return retained, len(required_cases)


def _diagnosis_case_passes(report: DiagnosisReport) -> bool:
  expected = report.incident.expected_outcome
  if expected is None:
    return False
  actions: set[str] = {match.action for match in report.matches}
  return _expected_outcome_passes(expected, actions)


def _diagnosis_cases_retained(
  baseline: tuple[DiagnosisReport, ...],
  revised: tuple[DiagnosisReport, ...],
  *,
  target_example_hash: str | None,
) -> tuple[bool, int]:
  required_reports = [
    (index, report)
    for index, report in enumerate(baseline)
    if _diagnosis_case_passes(report)
    or content_hash(report.incident.text) == target_example_hash
  ]
  retained: bool = bool(required_reports) and all(
    index < len(revised)
    and revised[index].incident == report.incident
    and (
      _diagnosis_case_passes(revised[index])
      or revised[index].incident.expected_outcome is None
    )
    for index, report in required_reports
  )
  return retained, len(required_reports)


def _natural_finding_signatures(
  findings: list[Finding],
) -> set[tuple[str, tuple[str, ...]]]:
  return {(finding.bug_type, tuple(sorted(finding.rule_ids))) for finding in findings}


def _natural_rule_semantics(model: SymbolicModel) -> dict[str, tuple[object, ...]]:
  semantics: dict[str, tuple[object, ...]] = {}
  for rule in model.rules:
    predicate_names = {
      literal.predicate for clause in rule.condition for literal in clause
    }
    predicate_meanings = tuple(
      sorted((name, model.predicates[name]) for name in predicate_names)
    )
    semantics[rule.rule_id] = (rule.decision, rule.condition, predicate_meanings)
  return semantics


class ObservedBackend:
  def __init__(
    self,
    backend: JudgeBackend,
    *,
    target: TargetConfig,
    call_numbers: itertools.count[int],
    deadline: float,
  ) -> None:
    self._backend: JudgeBackend = backend
    self._target: TargetConfig = target
    self._call_numbers: itertools.count[int] = call_numbers
    self._deadline: float = deadline

  def _log_fields(
    self,
    *,
    call_number: int,
    started: float,
    max_tokens: int,
  ) -> dict[str, object]:
    return {
      "provider": self._target.provider,
      "model": self._target.judge_model,
      "call_number": call_number,
      "latency_ms": round((time.perf_counter() - started) * 1_000),
      "requested_max_tokens": max_tokens,
    }

  def judge(self, prompt: str, *, max_tokens: int = 8192) -> JudgeResponse:
    if time.perf_counter() >= self._deadline:
      raise RuntimeError("proposed-fix generation exceeded its time budget")
    call_number: int = next(self._call_numbers)
    started: float = time.perf_counter()
    try:
      response: JudgeResponse = self._backend.judge(prompt, max_tokens=max_tokens)
    except Exception as exc:
      logger.exception(
        "proposed-fix model call failed",
        extra=self._log_fields(
          call_number=call_number, started=started, max_tokens=max_tokens
        ),
      )
      raise RuntimeError("model provider call failed; see server logs") from exc
    log_fields = self._log_fields(
      call_number=call_number, started=started, max_tokens=max_tokens
    )
    log_fields["output_characters"] = len(response.text)
    if response.usage is not None:
      log_fields.update(response.usage.to_dict())
    logger.info("proposed-fix model call completed", extra=log_fields)
    return response


@dataclass(frozen=True)
class _FixValidation:
  checks: tuple[ValidationCheck, ...]
  reasons: tuple[UnavailableReason, ...]


def _validation_check(code: str, label: str, passed: bool) -> ValidationCheck:
  return ValidationCheck(
    code=code,
    label=label,
    status=ValidationStatus.PASSED if passed else ValidationStatus.FAILED,
  )


def _validate_regex_fix(
  *,
  baseline: ScanResult,
  revised: str,
  specification_text: str,
  specification_name: str,
  taxonomy_text: str | None,
  violations_text: str | None,
  workflow: AnalysisWorkflow,
  target: TargetConfig,
  backend_factory: BackendFactory,
  fingerprint: FindingFingerprint,
) -> _FixValidation:
  baseline_scan: ScanResult = (
    baseline
    if workflow == "scan"
    else bugs(
      specification_text,
      specification_name=specification_name,
      taxonomy_text=taxonomy_text,
      model=target,
      backend_factory=backend_factory,
    )
  )
  revised_scan: ScanResult = bugs(
    revised,
    specification_name=specification_name,
    taxonomy_text=taxonomy_text,
    model=target,
    backend_factory=backend_factory,
  )
  revised_result: ScanResult = (
    diagnose_specification(
      revised,
      cast("str", violations_text),
      specification_name=specification_name,
    )
    if workflow == "diagnose"
    else revised_scan
  )
  revised_signatures = _finding_signatures(revised_result)
  target_removed: bool = fingerprint.signature() not in revised_signatures
  no_new_workflow_findings: bool = not (
    revised_signatures - _finding_signatures(baseline)
  )
  no_new_scan_findings: bool = not (
    _finding_signatures(revised_scan) - _finding_signatures(baseline_scan)
  )
  no_new_findings: bool = no_new_workflow_findings and no_new_scan_findings

  scan_examples_pass: bool = False
  scan_case_count: int = 0
  if baseline_scan.regex_report is not None and revised_scan.regex_report is not None:
    scan_examples_pass, scan_case_count = regex_examples_retained(
      baseline_scan.regex_report,
      revised_scan.regex_report,
      target_example_hash=fingerprint.example_content_hash,
    )

  diagnosis_examples_pass: bool = False
  diagnosis_case_count: int = 0
  if (
    workflow == "diagnose"
    and baseline.diagnosis_reports is not None
    and revised_result.diagnosis_reports is not None
  ):
    diagnosis_examples_pass, diagnosis_case_count = _diagnosis_cases_retained(
      baseline.diagnosis_reports,
      revised_result.diagnosis_reports,
      target_example_hash=fingerprint.example_content_hash,
    )

  trusted_case_count: int = scan_case_count + diagnosis_case_count
  examples_pass: bool = (scan_case_count == 0 or scan_examples_pass) and (
    diagnosis_case_count == 0 or diagnosis_examples_pass
  )
  checks: tuple[ValidationCheck, ...] = (
    _validation_check("target_removed", "Target finding removed", target_removed),
    _validation_check(
      "examples_retained",
      f"{trusted_case_count} trusted cases retained",
      examples_pass,
    ),
    _validation_check("no_new_findings", "No new supported findings", no_new_findings),
  )
  reasons: list[UnavailableReason] = []
  if not target_removed:
    reasons.append(UnavailableReason.TARGET_FINDING_REMAINS)
  if not examples_pass:
    reasons.append(UnavailableReason.REGRESSION)
  if not no_new_findings:
    reasons.append(UnavailableReason.NEW_FINDINGS)
  return _FixValidation(checks=checks, reasons=tuple(reasons))


def _validate_natural_fix(
  *,
  baseline: ScanResult,
  revised: str,
  taxonomy_text: str | None,
  finding: BugFinding,
  fingerprint: FindingFingerprint,
  backend: JudgeBackend,
  trusted_examples: tuple[TrustedPolicyExample, ...],
) -> _FixValidation:
  if baseline.symbolic_model is None:
    raise RuntimeError("natural-language baseline is missing its symbolic model")
  revised_guardrail: Guardrail = load_guardrail_text(revised)
  taxonomy: object | None = (
    cast("object", yaml.safe_load(taxonomy_text)) if taxonomy_text is not None else None
  )
  try:
    first_model: SymbolicModel = SemanticExtractor(backend).extract(
      revised_guardrail, taxonomy
    )
    second_model: SymbolicModel = SemanticExtractor(backend).extract(
      revised_guardrail, taxonomy
    )
  except (TypeError, ValueError) as exc:
    raise RepairGenerationError(
      f"invalid proposed-fix semantic extraction: {exc}"
    ) from exc

  first_findings: list[Finding] = SymbolicAnalyzer(
    first_model, revised_guardrail
  ).analyze()
  second_findings: list[Finding] = SymbolicAnalyzer(
    second_model, revised_guardrail
  ).analyze()
  stable: bool = first_model == second_model and _natural_finding_signatures(
    first_findings
  ) == _natural_finding_signatures(second_findings)
  evaluator = PolicyExampleEvaluator(backend)
  try:
    first_outcomes = evaluator.evaluate(revised, trusted_examples)
    second_outcomes = evaluator.evaluate(revised, trusted_examples)
  except (TypeError, ValueError) as exc:
    raise RepairGenerationError(
      f"invalid proposed-fix example evaluation: {exc}"
    ) from exc
  stable = stable and first_outcomes == second_outcomes
  examples_pass: bool = stable and all(
    outcome[0] == example.should_block
    for outcome, example in zip(first_outcomes, trusted_examples, strict=True)
  )

  baseline_signatures: set[tuple[str, tuple[str, ...]]] = {
    (item.bug_type, tuple(sorted(item.rule_ids))) for item in baseline.findings
  }
  revised_signatures = _natural_finding_signatures(first_findings)
  target_removed: bool = (
    fingerprint.bug_type,
    fingerprint.rule_ids,
  ) not in revised_signatures
  no_new_findings: bool = not (revised_signatures - baseline_signatures)
  baseline_semantics = _natural_rule_semantics(baseline.symbolic_model)
  revised_semantics = _natural_rule_semantics(first_model)
  unaffected_preserved: bool = all(
    revised_semantics.get(rule_id) == semantics
    for rule_id, semantics in baseline_semantics.items()
    if rule_id not in finding.rule_ids
  )
  checks: tuple[ValidationCheck, ...] = (
    _validation_check("stable_extraction", "Stable symbolic extraction", stable),
    _validation_check(
      "examples_retained",
      f"{len(trusted_examples)} trusted examples retained",
      examples_pass,
    ),
    _validation_check(
      "unaffected_rules",
      "Unaffected rule semantics preserved",
      unaffected_preserved,
    ),
    _validation_check("target_removed", "Target finding removed", target_removed),
    _validation_check("no_new_findings", "No new supported findings", no_new_findings),
  )
  reasons: list[UnavailableReason] = []
  if not stable:
    reasons.append(UnavailableReason.UNSTABLE_EXTRACTION)
  if not examples_pass or not unaffected_preserved:
    reasons.append(UnavailableReason.REGRESSION)
  if not target_removed:
    reasons.append(UnavailableReason.TARGET_FINDING_REMAINS)
  if not no_new_findings:
    reasons.append(UnavailableReason.NEW_FINDINGS)
  return _FixValidation(
    checks=checks,
    reasons=tuple(dict.fromkeys(reasons)),
  )


def propose_fix(
  specification_text: str,
  *,
  specification_name: str,
  taxonomy_text: str | None,
  violations_text: str | None,
  workflow: AnalysisWorkflow,
  finding_id: str,
  expected_content_hash: str,
  target: TargetConfig,
  backend_factory: BackendFactory = create_backend,
) -> ProposedFix:
  """Generate and validate one concrete specification patch.

  Raises:
    LookupError: if the requested finding or analysis binding is stale.
    RuntimeError: if a configured provider cannot execute.
    TypeError: if an input or provider response has the wrong shape.
    ValueError: if an input or provider response is invalid.
    yaml.YAMLError: if YAML input is malformed.
  """
  call_numbers = itertools.count(1)
  deadline: float = time.perf_counter() + 300

  def observed_backend_factory(config: TargetConfig) -> JudgeBackend:
    return ObservedBackend(
      backend_factory(config),
      target=config,
      call_numbers=call_numbers,
      deadline=deadline,
    )

  if workflow == "diagnose":
    if violations_text is None:
      raise ValueError("violations are required for diagnosis proposed fixes")
    baseline: ScanResult = diagnose_specification(
      specification_text,
      violations_text,
      specification_name=specification_name,
    )
  else:
    baseline = bugs(
      specification_text,
      specification_name=specification_name,
      taxonomy_text=taxonomy_text,
      model=target,
      backend_factory=observed_backend_factory,
    )
  if baseline.content_hash != expected_content_hash:
    raise LookupError("analysis inputs changed; run the analysis again")
  try:
    finding: BugFinding = next(
      item for item in baseline.findings if item.id == finding_id
    )
  except StopIteration as exc:
    raise LookupError("finding is no longer present; run the analysis again") from exc
  fingerprint: FindingFingerprint = _finding_fingerprint(
    finding,
    specification_kind=baseline.specification_kind,
    workflow=workflow,
  )
  checks: list[ValidationCheck] = []
  trusted_policy_examples: tuple[TrustedPolicyExample, ...] = ()
  if baseline.specification_kind == "natural_language":
    guardrail: Guardrail = load_guardrail_text(specification_text)
    taxonomy_examples = (
      parse_taxonomy_policy_examples(taxonomy_text) if taxonomy_text is not None else ()
    )
    trusted_policy_examples = merge_policy_examples(
      guardrail.examples, taxonomy_examples
    )
    if not trusted_policy_examples:
      checks.append(
        ValidationCheck(
          code="trusted_examples",
          label="Trusted examples available",
          status=ValidationStatus.FAILED,
        )
      )
      return _unavailable_fix(
        fingerprint=fingerprint,
        original_content_hash=baseline.content_hash,
        target=target,
        checks=checks,
        reasons=(UnavailableReason.INSUFFICIENT_EVIDENCE,),
      )

  backend: JudgeBackend = observed_backend_factory(target)
  affected_rules, prompt_examples = _affected_rules(
    specification_text,
    kind=baseline.specification_kind,
    rule_ids=finding.rule_ids,
  )
  if baseline.specification_kind == "natural_language":
    prompt_examples = [item.to_dict() for item in trusted_policy_examples]
  candidate: CandidateEdit = RepairCandidateGenerator(backend).generate(
    specification_kind=baseline.specification_kind,
    finding=finding.to_dict(),
    affected_rules=affected_rules,
    examples=prompt_examples,
    expected_rule_ids=finding.rule_ids,
  )
  ranges: dict[str, tuple[int, int]] = {
    unit.rule_id: (unit.start, unit.end) for unit in baseline.document.structural_units
  }
  try:
    revised: str = apply_candidate_edit(
      specification_text,
      candidate=candidate,
      specification_kind=baseline.specification_kind,
      rule_ranges=ranges,
    )
    revised_validation: ValidationResult = validate_specification(
      revised, name=specification_name
    )
    if (
      revised_validation.specification_kind != baseline.specification_kind
      or revised_validation.name != baseline.name
    ):
      raise ValueError("candidate changed specification identity")
  except (TypeError, ValueError, yaml.YAMLError) as exc:
    checks.append(
      ValidationCheck(
        code="valid_specification",
        label="Specification valid",
        status=ValidationStatus.FAILED,
        detail=str(exc),
      )
    )
    return _unavailable_fix(
      fingerprint=fingerprint,
      original_content_hash=baseline.content_hash,
      target=target,
      checks=checks,
      reasons=(UnavailableReason.INVALID_CANDIDATE,),
    )
  checks.append(
    ValidationCheck(
      code="valid_specification",
      label="YAML and specification valid",
      status=ValidationStatus.PASSED,
    )
  )

  if baseline.specification_kind == "regex":
    fix_validation: _FixValidation = _validate_regex_fix(
      baseline=baseline,
      revised=revised,
      specification_text=specification_text,
      specification_name=specification_name,
      taxonomy_text=taxonomy_text,
      violations_text=violations_text,
      workflow=workflow,
      target=target,
      backend_factory=observed_backend_factory,
      fingerprint=fingerprint,
    )
  else:
    fix_validation = _validate_natural_fix(
      baseline=baseline,
      revised=revised,
      taxonomy_text=taxonomy_text,
      finding=finding,
      fingerprint=fingerprint,
      backend=backend,
      trusted_examples=trusted_policy_examples,
    )
  checks.extend(fix_validation.checks)
  if fix_validation.reasons:
    return _unavailable_fix(
      fingerprint=fingerprint,
      original_content_hash=baseline.content_hash,
      target=target,
      checks=checks,
      reasons=fix_validation.reasons,
    )

  return ProposedFix(
    status=ProposedFixStatus.VALIDATED,
    specification_kind=baseline.specification_kind,
    finding_fingerprint=fingerprint,
    original_content_hash=baseline.content_hash,
    provider=target.provider,
    model=target.judge_model,
    revised_specification=revised,
    unified_diff=unified_specification_diff(
      specification_text, revised, filename=specification_name
    ),
    explanation=candidate.explanation,
    validation_checks=tuple(checks),
  )
