"""Independent audit dimensions; no replacement verdict."""

from dataclasses import asdict
from time import monotonic
from typing import Any

import z3

from .models import AuditInput, Truth, require
from .solver import Engine, compile_expr

_AUDIT_BUDGET_MS = 5_000


def aggregate(statuses: list[str]) -> str:
  if "REFUTED" in statuses:
    return "VIOLATED"
  if statuses and all(status == "ENTAILED" for status in statuses):
    return "SATISFIED"
  return "UNKNOWN"


def audit(case: AuditInput, timeout_ms: int = 1000) -> dict[str, Any]:
  """Audit a validated replay within per-query and total solver budgets.

  Raises:
    ValidationError: if the per-query timeout is outside the supported range.
  """
  require(
    type(timeout_ms) is int and 1 <= timeout_ms <= 60000, "timeout_ms must be 1..60000"
  )
  report: dict[str, Any] = {
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
        "evidence": [asdict(evidence) for evidence in finding.evidence],
        "explanation": finding.explanation,
      }
      for finding in case.findings
    ],
  }
  if case.extraction["translation_status"] != "VALID":
    report.update(
      status="TRANSLATION_INVALID"
      if case.extraction["translation_status"] == "INVALID"
      else "TRANSLATION_UNKNOWN",
      protocol_adherent="UNKNOWN",
      procedural_adherence="UNKNOWN",
      rationale_verdict=None,
    )
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
    ("protocol:decision", verdict == compile_expr(case.decision, symbols))
  )
  protocol_engine = Engine(declarations, symbols, timeout_ms, deadline)
  facts = [
    (
      f"finding:{index}:{finding_entry.atom}",
      symbols[finding_entry.atom] == (finding_entry.value == Truth.TRUE),
    )
    for index, finding_entry in enumerate(case.findings)
    if finding_entry.value != Truth.UNKNOWN
  ]
  # Facts-only queries keep direct evidence independent of inconsistent protocol rules.
  evidence_engine = Engine(facts, symbols, timeout_ms, deadline)
  base = Engine(
    declarations + facts, {**symbols, "@verdict": verdict}, timeout_ms, deadline
  )
  observed = verdict if case.verdict == "PASS" else z3.Not(verdict)
  verdict_result = base.query(observed)
  requirements: list[dict[str, Any]] = []
  for requirement in case.requirements:
    compliance = evidence_engine.query(compile_expr(requirement.expr, symbols))
    addressed = (
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
  grounding = [
    {
      "judge_atom": judge_atom,
      "context_atom": context_atom,
      **evidence_engine.query(symbols[judge_atom] == symbols[context_atom]),
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
  report.update(
    status=status,
    protocol_adherent=overall,
    procedural_adherence=procedural,
    grounding_correctness=grounding_status,
    rationale_verdict=verdict_result,
    rationale_verdict_consistent={
      "ENTAILED": True,
      "UNDERDETERMINED": True,
      "REFUTED": False,
    }.get(verdict_result["status"]),
    verdict_entailed={"ENTAILED": True, "UNDERDETERMINED": False, "REFUTED": False}.get(
      verdict_result["status"]
    ),
    opposite_verdict_entailed={
      "ENTAILED": False,
      "UNDERDETERMINED": False,
      "REFUTED": True,
    }.get(verdict_result["status"]),
    expected_verdict_under_stated_findings=expected_verdict,
    coverage={
      "addressed": coverage_statuses.count("ENTAILED"),
      "total": len(requirements),
      "ratio": coverage_statuses.count("ENTAILED") / len(requirements)
      if requirements
      else None,
      "unknown": sum(
        status not in ("ENTAILED", "REFUTED") for status in coverage_statuses
      ),
    },
    requirements=requirements,
    grounding_checks=grounding,
    violations=violations,
    unknown_atoms=[
      judge_atom.id for judge_atom in case.atoms if judge_atom.id not in known
    ],
    solver={
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
    witness_note="Models complete unknown atoms with possible values; these are not extracted observations. UNSAT cores are sufficient conflicts, not guaranteed minimal.",
  )
  return report
