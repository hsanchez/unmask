"""Bounded judge replay validation, protocol auditing, and source-aware reports."""

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import NotRequired, TypedDict, cast

import z3

from .models import SourceDocument, canonical_json_hash
from .solver import (
  Engine,
  QueryResult,
  conjunction,
  disjunction,
  negation,
  require_bool_ref,
)

MAX_BYTES = 1000000
MAX_ATOMS = 128
MAX_NODES = 1024
MAX_DEPTH = 16

_AUDIT_BUDGET_MS = 5_000

AUDITOR_VERSION = "1"
AUDIT_SCHEMA_VERSION = 1


class ValidationError(ValueError):
  """A replay fails structural, resource or evidence validation."""


class Truth(StrEnum):
  """Known findings and explicitly unconstrained semantic facts."""

  TRUE = "TRUE"
  FALSE = "FALSE"
  UNKNOWN = "UNKNOWN"


def require(ok: bool, message: str) -> None:
  if not ok:
    raise ValidationError(message)


def validate_object(
  value: object,
  required: set[str],
  optional: set[str] | frozenset[str] | None = None,
) -> dict[str, object]:
  if not isinstance(value, dict):
    raise ValidationError("expected JSON object")
  data = cast("dict[str, object]", value)
  allowed = required | (optional or set())
  require(required <= data.keys(), f"missing fields: {required - data.keys()}")
  require(
    data.keys() <= allowed,
    f"unexpected fields: {data.keys() - allowed}",
  )
  return data


def require_text(value: object) -> str:
  if not isinstance(value, str) or not value.strip():
    raise ValidationError("expected nonempty string")
  try:
    _ = value.encode("utf-8")
  except UnicodeEncodeError as exc:
    raise ValidationError("text contains invalid Unicode") from exc
  return value


def validate_items(value: object, limit: int = MAX_ATOMS) -> list[object]:
  if not isinstance(value, list):
    raise ValidationError(f"expected list with at most {limit} items")
  items = cast("list[object]", value)
  require(len(items) <= limit, f"expected list with at most {limit} items")
  return items


@dataclass(frozen=True)
class Expr:
  """A bounded Boolean expression tree."""

  op: str
  args: tuple[Expr, ...] = ()
  atom: str | None = None
  value: bool | None = None

  def referenced_atoms(self) -> set[str]:
    if self.atom is not None:
      return {self.atom}
    names: set[str] = set()
    for child in self.args:
      names.update(child.referenced_atoms())
    return names

  @classmethod
  def parse(
    cls, value: object, atoms: set[str], budget: list[int], depth: int = 0
  ) -> Expr:
    budget[0] += 1
    require(
      budget[0] <= MAX_NODES and depth <= MAX_DEPTH,
      "expression resource limit exceeded",
    )
    if not isinstance(value, dict):
      raise ValidationError("expression must be object")
    data = cast("dict[str, object]", value)
    if "atom" in data:
      _ = validate_object(data, {"atom"})
      atom = data["atom"]
      if not isinstance(atom, str) or atom not in atoms:
        raise ValidationError("undeclared atom")
      return cls("atom", atom=atom)
    if "const" in data:
      _ = validate_object(data, {"const"})
      constant = data["const"]
      if type(constant) is not bool:
        raise ValidationError("const must be Boolean")
      return cls("const", value=constant)
    _ = validate_object(data, {"op", "args"})
    op = data["op"]
    if not isinstance(op, str):
      raise ValidationError("unsupported operator")
    require(
      op in {"and", "or", "not", "implies", "iff"},
      "unsupported operator",
    )
    args = validate_items(data["args"])
    require(
      op in {"and", "or"}
      and len(args) >= 1
      or (op == "not" and len(args) == 1)
      or (op in {"implies", "iff"} and len(args) == 2),
      "invalid operator arity",
    )
    return cls(
      op,
      tuple(cls.parse(atom_entry, atoms, budget, depth + 1) for atom_entry in args),
    )


@dataclass(frozen=True)
class Atom:
  """A declared semantic or procedural proposition."""

  id: str
  role: str
  description: str


@dataclass(frozen=True)
class Evidence:
  """An exact half-open quotation in a replay source."""

  source: str
  start: int
  end: int
  quote: str


@dataclass(frozen=True)
class Finding:
  """A supplied annotation and its observable evidence."""

  atom: str
  value: Truth
  evidence: tuple[Evidence, ...]
  explanation: str


@dataclass(frozen=True)
class Rule:
  """A protocol constraint or obligation with optional coverage."""

  id: str
  description: str
  expr: Expr
  addressed: Expr | None = None


@dataclass(frozen=True)
class AuditInput:
  """A replay separating protocol, judge observations, grounding and gold."""

  id: str
  context: str
  response: str
  rationale: str
  verdict: str
  gold_verdict: str | None
  protocol_text: str
  atoms: tuple[Atom, ...]
  decision: Expr
  constraints: tuple[Rule, ...]
  requirements: tuple[Rule, ...]
  findings: tuple[Finding, ...]
  grounding_pairs: tuple[tuple[str, str], ...]
  extraction: dict[str, str]

  @classmethod
  def from_json(cls, text: str) -> AuditInput:
    """Parse replay JSON with bounded resources and unique keys.

    Raises:
      ValidationError: if JSON, resource bounds or evidence are invalid.
    """
    try:
      byte_count = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
      raise ValidationError("JSON input contains invalid Unicode") from exc
    require(byte_count <= MAX_BYTES, "input exceeds byte limit")

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
      result: dict[str, object] = {}
      for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
      return result

    def reject_constant(value: str) -> None:
      raise ValidationError(f"invalid JSON constant: {value}")

    try:
      data = cast(
        "object",
        json.loads(text, object_pairs_hook=unique, parse_constant=reject_constant),
      )
      return cls.parse(data)
    except (json.JSONDecodeError, RecursionError) as exc:
      raise ValidationError(f"invalid JSON: {exc}") from exc

  @classmethod
  def parse(cls, data: object) -> AuditInput:
    """Validate a decoded replay without interpreting natural language.

    Raises:
      ValidationError: if its structure or references are invalid.
    """
    data = validate_object(
      data,
      {
        "schema_version",
        "id",
        "context",
        "response",
        "rationale",
        "verdict",
        "protocol",
        "findings",
        "extraction",
      },
      {"gold_verdict", "grounding_pairs"},
    )
    require(
      type(data["schema_version"]) is int and data["schema_version"] == 1,
      "unsupported schema version",
    )
    identifier = require_text(data["id"])
    context = require_text(data["context"])
    response = require_text(data["response"])
    rationale = require_text(data["rationale"])
    verdict = require_text(data["verdict"])
    require(verdict in ("PASS", "FAIL"), "verdict must be PASS or FAIL")
    raw_gold = data.get("gold_verdict")
    require(raw_gold in (None, "PASS", "FAIL"), "invalid gold verdict")
    gold_verdict = require_text(raw_gold) if raw_gold is not None else None
    protocol = validate_object(
      data["protocol"], {"text", "atoms", "decision", "constraints", "requirements"}
    )
    protocol_text = require_text(protocol["text"])
    atom_entries = validate_items(protocol["atoms"])
    require(bool(atom_entries), "declare at least one atom")
    atoms: list[Atom] = []
    for raw_atom in atom_entries:
      atom_entry = validate_object(raw_atom, {"id", "role", "description"})
      atom_id = require_text(atom_entry["id"])
      require(
        re.fullmatch("[A-Za-z][A-Za-z0-9_]{0,63}", atom_id) is not None,
        "invalid atom id",
      )
      role = require_text(atom_entry["role"])
      require(role in ("judge", "context", "procedure"), "invalid atom role")
      atoms.append(Atom(atom_id, role, require_text(atom_entry["description"])))
    names = {atom_entry.id for atom_entry in atoms}
    require(len(names) == len(atoms), "duplicate atom id")
    roles = {atom_entry.id: atom_entry.role for atom_entry in atoms}
    budget = [0]
    decision = Expr.parse(protocol["decision"], names, budget)

    require(
      all(roles[name] != "context" for name in decision.referenced_atoms()),
      "decision may use judge/procedure findings, not independent context assessments",
    )
    seen_rules: set[str] = set()

    def parse_rules(key: str) -> tuple[Rule, ...]:
      result: list[Rule] = []
      for raw_rule in validate_items(protocol[key]):
        rule_entry = validate_object(
          raw_rule,
          {"id", "description", "expr"},
          {"addressed"} if key == "requirements" else set(),
        )
        rule_id = require_text(rule_entry["id"])
        description = require_text(rule_entry["description"])
        require(rule_id not in seen_rules, "duplicate rule id")
        seen_rules.add(rule_id)
        expression = Expr.parse(rule_entry["expr"], names, budget)
        if key == "constraints":
          require(
            all(roles[name] != "context" for name in expression.referenced_atoms()),
            "protocol constraints cannot bind independent context assessments",
          )
        addressed = (
          Expr.parse(rule_entry["addressed"], names, budget)
          if "addressed" in rule_entry
          else None
        )
        result.append(Rule(rule_id, description, expression, addressed))
      return tuple(result)

    constraints, requirements = (
      parse_rules("constraints"),
      parse_rules("requirements"),
    )
    findings: list[Finding] = []
    sources = {
      "context": context,
      "response": response,
      "rationale": rationale,
    }
    for raw_finding in validate_items(data["findings"], MAX_ATOMS * 2):
      finding_entry = validate_object(
        raw_finding, {"atom", "value", "evidence", "explanation"}
      )
      atom = require_text(finding_entry["atom"])
      require(atom in names, "finding refers to undeclared atom")
      truth = require_text(finding_entry["value"])
      require(truth in ("TRUE", "FALSE", "UNKNOWN"), "invalid truth value")
      explanation = require_text(finding_entry["explanation"])
      evidence: list[Evidence] = []
      for raw_evidence in validate_items(finding_entry["evidence"], 16):
        evidence_data = validate_object(
          raw_evidence, {"source", "start", "end", "quote"}
        )
        source = require_text(evidence_data["source"])
        require(source in sources, "invalid evidence source")
        start, end = evidence_data["start"], evidence_data["end"]
        if type(start) is not int or type(end) is not int:
          raise ValidationError("evidence offsets must be integers")
        require(
          0 <= start < end <= len(sources[source]),
          "evidence offsets out of bounds",
        )
        quote = evidence_data["quote"]
        if not isinstance(quote, str) or sources[source][start:end] != quote:
          raise ValidationError("evidence quote mismatch")
        expected = (
          {"context", "response"} if roles[atom] == "context" else {"rationale"}
        )
        require(
          source in expected,
          "evidence source incompatible with atom role",
        )
        evidence.append(Evidence(source, start, end, quote))
      require(
        truth == "UNKNOWN" or bool(evidence),
        "known findings require evidence",
      )
      findings.append(Finding(atom, Truth(truth), tuple(evidence), explanation))
    pairs: list[tuple[str, str]] = []
    for raw_pair in validate_items(data.get("grounding_pairs", [])):
      pair = validate_items(raw_pair, 2)
      require(
        len(pair) == 2
        and all(isinstance(name, str) and name in roles for name in pair),
        "invalid grounding pair",
      )
      judge_atom, context_atom = require_text(pair[0]), require_text(pair[1])
      require(
        roles[judge_atom] == "judge" and roles[context_atom] == "context",
        "grounding pairs must be judge/context",
      )
      pairs.append((judge_atom, context_atom))
    extraction_data = validate_object(
      data["extraction"], {"provider", "translation_status"}, {"notes"}
    )
    extraction = {
      "provider": require_text(extraction_data["provider"]),
      "translation_status": require_text(extraction_data["translation_status"]),
    }
    require(
      extraction["translation_status"] in ("VALID", "INVALID", "UNKNOWN"),
      "invalid translation status",
    )
    if "notes" in extraction_data:
      extraction["notes"] = require_text(extraction_data["notes"])
    return cls(
      identifier,
      context,
      response,
      rationale,
      verdict,
      gold_verdict,
      protocol_text,
      tuple(atoms),
      decision,
      constraints,
      requirements,
      tuple(findings),
      tuple(pairs),
      extraction,
    )


def compile_expr(
  expression: Expr,
  symbols: dict[str, z3.BoolRef],
  *,
  context: z3.Context | None = None,
) -> z3.BoolRef:
  """Compile a validated expression, including ground formulas without symbols.

  An omitted context is inferred from symbols, or uses Z3's default context
  when the symbol table is empty.

  Raises:
    KeyError: if a referenced atom has no symbol.
    ValueError: if the expression operator is unsupported.
    z3.Z3Exception: if a referenced symbol belongs to another context.
  """
  if context is None:
    context = next(iter(symbols.values())).ctx if symbols else z3.main_ctx()
  if expression.op == "atom":
    symbol = symbols[expression.atom or ""]
    if symbol.ctx != context:
      raise z3.Z3Exception("context mismatch: atom must belong to the selected context")
    return symbol
  if expression.op == "const":
    return z3.BoolVal(expression.value, ctx=context)
  arguments = [
    compile_expr(child, symbols, context=context) for child in expression.args
  ]
  if expression.op == "and":
    return conjunction(*arguments, context=context)
  if expression.op == "or":
    return disjunction(*arguments, context=context)
  if expression.op == "not":
    return negation(arguments[0])
  if expression.op == "implies":
    return z3.Implies(*arguments)
  if expression.op == "iff":
    return require_bool_ref(cast("object", arguments[0] == arguments[1]))
  raise ValueError("unvalidated expression")


class EvidenceReport(TypedDict):
  """The source span serialized for an observation."""

  source: str
  start: int
  end: int
  quote: str


class FindingReport(TypedDict):
  """A serialized finding with its supporting evidence."""

  atom: str
  value: str
  evidence: list[EvidenceReport]
  explanation: str


class RequirementResult(TypedDict):
  """Independent compliance and coverage results for an obligation."""

  id: str
  description: str
  compliance: QueryResult
  coverage: QueryResult


class GroundingResult(QueryResult):
  """A proposition comparing judge and independent context observations."""

  judge_atom: str
  context_atom: str


class CoverageReport(TypedDict):
  """Counts of observable obligation coverage."""

  addressed: int
  total: int
  ratio: float | None
  unknown: int


class SolverReport(TypedDict):
  """Solver configuration, base consistency, and conflict evidence."""

  name: str
  version: str
  timeout_ms: int
  audit_budget_ms: int
  protocol: str
  facts: str
  base: str
  unsat_core: list[str]
  reason_unknown: str | None


class AuditReport(TypedDict):
  """Replay results; solver details exist only for valid translations."""

  schema_version: int
  id: str
  scope: str
  extraction: dict[str, str]
  observed_verdict: str
  gold_verdict: str | None
  verdict_correct: bool | None
  findings: list[FindingReport]
  status: str
  protocol_adherent: str
  procedural_adherence: str
  rationale_verdict: QueryResult | None
  grounding_correctness: NotRequired[str]
  rationale_verdict_consistent: NotRequired[bool | None]
  verdict_entailed: NotRequired[bool | None]
  opposite_verdict_entailed: NotRequired[bool | None]
  expected_verdict_under_stated_findings: NotRequired[str | None]
  coverage: NotRequired[CoverageReport]
  requirements: NotRequired[list[RequirementResult]]
  grounding_checks: NotRequired[list[GroundingResult]]
  violations: NotRequired[list[dict[str, str]]]
  unknown_atoms: NotRequired[list[str]]
  solver: NotRequired[SolverReport]
  witness_note: NotRequired[str]


def aggregate(statuses: list[str]) -> str:
  if "REFUTED" in statuses:
    return "VIOLATED"
  if statuses and all(status == "ENTAILED" for status in statuses):
    return "SATISFIED"
  return "UNKNOWN"


def audit(case: AuditInput, timeout_ms: int = 1000) -> AuditReport:
  """Audit a validated replay within per-query and total solver budgets.

  Raises:
    ValidationError: if the per-query timeout is outside the supported range.
  """
  require(
    type(timeout_ms) is int and 1 <= timeout_ms <= 60000, "timeout_ms must be 1..60000"
  )
  report: AuditReport = {
    "schema_version": 1,
    "id": case.id,
    "scope": "Observable evaluation only; conditional on supplied protocol translation and semantic findings.",
    "extraction": case.extraction,
    "observed_verdict": case.verdict,
    "gold_verdict": case.gold_verdict,
    "verdict_correct": None
    if case.gold_verdict is None
    else case.verdict == case.gold_verdict,
    "findings": [
      {
        "atom": finding.atom,
        "value": finding.value.value,
        "evidence": [
          {
            "source": evidence.source,
            "start": evidence.start,
            "end": evidence.end,
            "quote": evidence.quote,
          }
          for evidence in finding.evidence
        ],
        "explanation": finding.explanation,
      }
      for finding in case.findings
    ],
    "status": "TRANSLATION_INVALID"
    if case.extraction["translation_status"] == "INVALID"
    else "TRANSLATION_UNKNOWN",
    "protocol_adherent": "UNKNOWN",
    "procedural_adherence": "UNKNOWN",
    "rationale_verdict": None,
  }
  if case.extraction["translation_status"] != "VALID":
    return report
  context = z3.Context()
  deadline = monotonic() + _AUDIT_BUDGET_MS / 1_000
  symbols = {
    judge_atom.id: z3.Bool(f"atom:{judge_atom.id}", ctx=context)
    for judge_atom in case.atoms
  }
  verdict = z3.Bool("@verdict", ctx=context)
  declarations = [
    (f"protocol:{requirement.id}", compile_expr(requirement.expr, symbols))
    for requirement in case.constraints
  ]
  declarations.append(
    (
      "protocol:decision",
      require_bool_ref(cast("object", verdict == compile_expr(case.decision, symbols))),
    )
  )
  protocol_engine = Engine(declarations, symbols, timeout_ms, deadline)
  facts = [
    (
      f"finding:{index}:{finding_entry.atom}",
      require_bool_ref(
        cast(
          "object", symbols[finding_entry.atom] == (finding_entry.value == Truth.TRUE)
        )
      ),
    )
    for index, finding_entry in enumerate(case.findings)
    if finding_entry.value != Truth.UNKNOWN
  ]
  # Facts-only queries keep direct evidence independent of inconsistent protocol rules.
  evidence_engine = Engine(facts, symbols, timeout_ms, deadline)
  base = Engine(
    declarations + facts, {**symbols, "@verdict": verdict}, timeout_ms, deadline
  )
  observed = verdict if case.verdict == "PASS" else negation(verdict)
  verdict_result = base.query(observed)
  requirements: list[RequirementResult] = []
  for requirement in case.requirements:
    compliance = evidence_engine.query(compile_expr(requirement.expr, symbols))
    addressed: QueryResult = (
      evidence_engine.query(compile_expr(requirement.addressed, symbols))
      if requirement.addressed
      else {"status": "NOT_SPECIFIED"}
    )
    requirements.append(
      {
        "id": requirement.id,
        "description": requirement.description,
        "compliance": compliance,
        "coverage": addressed,
      }
    )
  grounding: list[GroundingResult] = [
    {
      "judge_atom": judge_atom,
      "context_atom": context_atom,
      **evidence_engine.query(
        require_bool_ref(cast("object", symbols[judge_atom] == symbols[context_atom]))
      ),
    }
    for judge_atom, context_atom in case.grounding_pairs
  ]
  compliance_statuses = [
    requirement["compliance"]["status"] for requirement in requirements
  ]
  coverage_statuses = [
    requirement["coverage"]["status"] for requirement in requirements
  ]
  procedural = aggregate(compliance_statuses + coverage_statuses)
  grounding_status = aggregate(
    [grounding_check["status"] for grounding_check in grounding]
  )
  combined = (
    compliance_statuses
    + coverage_statuses
    + [verdict_result["status"]]
    + [grounding_check["status"] for grounding_check in grounding]
  )
  overall = aggregate(
    combined + (["UNKNOWN"] if not requirements or not grounding else [])
  )
  status = "OK"
  if protocol_engine.base == "unsat":
    status = "INCONSISTENT_PROTOCOL"
  elif evidence_engine.base == "unsat":
    status = "INCONSISTENT_FINDINGS"
  elif base.base == "unsat":
    status = "INCONSISTENT_BASE"
  elif "unknown" in (protocol_engine.base, evidence_engine.base, base.base) or any(
    status == "SOLVER_UNKNOWN" for status in combined
  ):
    status = "SOLVER_UNKNOWN"
  if status != "OK":
    overall = "UNKNOWN"
  violations = [
    {"kind": "protocol_requirement", "id": requirement["id"]}
    for requirement in requirements
    if requirement["compliance"]["status"] == "REFUTED"
  ]
  violations += [
    {"kind": "missing_observable_coverage", "id": requirement["id"]}
    for requirement in requirements
    if requirement["coverage"]["status"] == "REFUTED"
  ]
  violations += [
    {"kind": "semantic_grounding_mismatch", "id": grounding_check["judge_atom"]}
    for grounding_check in grounding
    if grounding_check["status"] == "REFUTED"
  ]
  if verdict_result["status"] == "REFUTED":
    violations.append({"kind": "rationale_verdict_mismatch", "id": "verdict"})
  known = {
    finding_entry.atom
    for finding_entry in case.findings
    if finding_entry.value != Truth.UNKNOWN
  }
  expected_verdict: str | None = None
  if verdict_result["status"] == "ENTAILED":
    expected_verdict = case.verdict
  elif verdict_result["status"] == "REFUTED":
    expected_verdict = "FAIL" if case.verdict == "PASS" else "PASS"
  return {
    **report,
    "status": status,
    "protocol_adherent": overall,
    "procedural_adherence": procedural,
    "grounding_correctness": grounding_status,
    "rationale_verdict": verdict_result,
    "rationale_verdict_consistent": {
      "ENTAILED": True,
      "UNDERDETERMINED": True,
      "REFUTED": False,
    }.get(verdict_result["status"]),
    "verdict_entailed": {
      "ENTAILED": True,
      "UNDERDETERMINED": False,
      "REFUTED": False,
    }.get(verdict_result["status"]),
    "opposite_verdict_entailed": {
      "ENTAILED": False,
      "UNDERDETERMINED": False,
      "REFUTED": True,
    }.get(verdict_result["status"]),
    "expected_verdict_under_stated_findings": expected_verdict,
    "coverage": {
      "addressed": coverage_statuses.count("ENTAILED"),
      "total": len(requirements),
      "ratio": coverage_statuses.count("ENTAILED") / len(requirements)
      if requirements
      else None,
      "unknown": sum(
        status not in ("ENTAILED", "REFUTED") for status in coverage_statuses
      ),
    },
    "requirements": requirements,
    "grounding_checks": grounding,
    "violations": violations,
    "unknown_atoms": [
      judge_atom.id for judge_atom in case.atoms if judge_atom.id not in known
    ],
    "solver": {
      "name": "Z3",
      "version": z3.get_version_string(),
      "timeout_ms": timeout_ms,
      "audit_budget_ms": _AUDIT_BUDGET_MS,
      "protocol": protocol_engine.base,
      "facts": evidence_engine.base,
      "base": base.base,
      "unsat_core": base.core,
      "reason_unknown": base.reason,
    },
    "witness_note": "Models complete unknown atoms with possible values; these are not extracted observations. UNSAT cores are sufficient conflicts, not guaranteed minimal.",
  }


class AuditCheck(TypedDict):
  """A source-linked check rendered by the audit workspace."""

  id: str
  dimension: str
  summary: str
  status: str
  proof: QueryResult
  atoms: list[str]
  sourceReferences: list[dict[str, object]]


def _documents(case: AuditInput) -> list[dict[str, object]]:
  return [
    SourceDocument(
      id=role,
      role=role,
      name=name,
      raw_text=text,
      structural_units=(),
      media_type="text/plain",
    ).to_dict()
    for role, name, text in (
      ("protocol", "Protocol", case.protocol_text),
      ("context", "Context", case.context),
      ("response", "Candidate response", case.response),
      ("rationale", "Judge rationale", case.rationale),
    )
  ]


def _envelope(case: AuditInput, text: str) -> dict[str, object]:
  # Bind the original payload already checked by AuditInput.from_json, not a reconstruction.
  payload = cast("object", json.loads(text))
  binding = {
    "case": payload,
    "auditorVersion": AUDITOR_VERSION,
    "schemaVersion": AUDIT_SCHEMA_VERSION,
  }
  return {
    "schemaVersion": AUDIT_SCHEMA_VERSION,
    "auditorVersion": AUDITOR_VERSION,
    "workflow": "audit_judge",
    "name": case.id,
    "contentHash": canonical_json_hash(binding),
    "documents": _documents(case),
    "case": payload,
  }


def validate_audit_case(text: str) -> dict[str, object]:
  """Validate replay annotations and evidence without making model calls.

  Raises:
    ValueError: if the case fails bounded IR or evidence validation.
  """
  case = AuditInput.from_json(text)
  return _envelope(case, text)


def audit_judge_case(text: str) -> dict[str, object]:
  """Audit a replay and preserve the core report alongside UI checks.

  Raises:
    ValueError: if the case fails bounded IR or evidence validation.
    RuntimeError: if the solver cannot execute the audit.
  """
  case = AuditInput.from_json(text)
  try:
    report = audit(case)
  except z3.Z3Exception as exc:
    raise RuntimeError("judge audit solver failed") from exc
  result = _envelope(case, text)
  checks: list[AuditCheck] = []

  def add_check(
    identifier: str, dimension: str, title: str, proof: QueryResult, atoms: set[str]
  ) -> None:
    references: list[dict[str, object]] = []
    for index, finding in enumerate(case.findings):
      if finding.atom not in atoms:
        continue
      for evidence in finding.evidence:
        references.append(
          {
            "documentId": evidence.source,
            "start": evidence.start,
            "end": evidence.end,
            "quote": evidence.quote,
            "annotationId": f"finding:{index}:{finding.atom}",
          }
        )
    checks.append(
      {
        "id": identifier,
        "dimension": dimension,
        "summary": title,
        "status": proof["status"],
        "proof": proof,
        "atoms": sorted(atoms),
        "sourceReferences": references,
      }
    )

  for requirement, evaluation in zip(
    case.requirements, report.get("requirements", []), strict=False
  ):
    add_check(
      f"procedure:{requirement.id}",
      "procedure",
      requirement.description,
      evaluation["compliance"],
      requirement.expr.referenced_atoms(),
    )
    add_check(
      f"coverage:{requirement.id}",
      "coverage",
      requirement.description,
      evaluation["coverage"],
      requirement.addressed.referenced_atoms() if requirement.addressed else set(),
    )
  for index, grounding_evaluation in enumerate(report.get("grounding_checks", [])):
    atoms = {grounding_evaluation["judge_atom"], grounding_evaluation["context_atom"]}
    add_check(
      f"grounding:{index}",
      "grounding",
      "Compare judge finding with independent grounding",
      grounding_evaluation,
      atoms,
    )
  verdict = report.get("rationale_verdict")
  if verdict is not None:
    verdict_atoms = case.decision.referenced_atoms().union(
      *(rule.expr.referenced_atoms() for rule in case.constraints)
    )
    add_check(
      "verdict",
      "entailment",
      "Observed verdict follows from the supplied model",
      verdict,
      verdict_atoms,
    )
  unknown = [
    atom.id
    for atom in case.atoms
    if not any(
      finding.atom == atom.id and finding.value != "UNKNOWN"
      for finding in case.findings
    )
  ]
  result.update(
    {
      "status": "completed",
      "audit": report,
      "checks": checks,
      "summary": {
        "status": report["status"],
        "overall": report["protocol_adherent"],
        "coverage": aggregate(
          [item["status"] for item in checks if item["dimension"] == "coverage"]
        ),
        "procedure": report["procedural_adherence"],
        "grounding": report.get("grounding_correctness", "UNKNOWN"),
        "consistency": "COMPATIBLE"
        if report.get("rationale_verdict_consistent") is True
        else "INCOMPATIBLE"
        if report.get("rationale_verdict_consistent") is False
        else "UNKNOWN",
        "entailment": verdict["status"] if verdict else "NOT_CHECKED",
      },
      "unknownAtoms": unknown,
      "annotations": [
        {"id": f"finding:{index}:{finding['atom']}", **finding}
        for index, finding in enumerate(report["findings"])
      ],
      "limitations": [
        report["scope"],
        "Evidence spans validate quotations, not semantic interpretation. Translation validity is supplied by the annotator.",
        "Missing observable coverage does not establish what happened in hidden reasoning.",
        report.get(
          "witness_note", "No solver conclusions are available for this translation."
        ),
      ],
    }
  )
  return result
