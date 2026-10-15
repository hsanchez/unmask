"""Versioned replay case validation and source-aware audit reports."""

import hashlib
import json
from typing import Any

import z3

from .auditor import aggregate, audit
from .models import AuditInput

AUDITOR_VERSION = "1"
AUDIT_SCHEMA_VERSION = 1


def _documents(case: AuditInput) -> list[dict[str, Any]]:
  return [
    {
      "id": role,
      "role": role,
      "name": name,
      "mediaType": "text/plain",
      "rawText": text,
      "structuralUnits": [],
    }
    for role, name, text in (
      ("protocol", "Protocol", case.protocol_text),
      ("context", "Context", case.context),
      ("response", "Candidate response", case.response),
      ("rationale", "Judge rationale", case.rationale),
    )
  ]


def _envelope(case: AuditInput, text: str) -> dict[str, Any]:
  payload = json.loads(text)
  binding = json.dumps(
    {
      "case": payload,
      "auditorVersion": AUDITOR_VERSION,
      "schemaVersion": AUDIT_SCHEMA_VERSION,
    },
    sort_keys=True,
    ensure_ascii=False,
  )
  return {
    "schemaVersion": AUDIT_SCHEMA_VERSION,
    "auditorVersion": AUDITOR_VERSION,
    "workflow": "audit_judge",
    "name": case.id,
    "contentHash": hashlib.sha256(binding.encode("utf-8")).hexdigest(),
    "documents": _documents(case),
    "case": payload,
  }


def validate_audit_case(text: str) -> dict[str, Any]:
  """Validate replay annotations and evidence without making model calls.

  Raises:
    ValueError: if the case fails bounded IR or evidence validation.
  """
  case = AuditInput.from_json(text)
  return _envelope(case, text)


def audit_judge_case(text: str) -> dict[str, Any]:
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
  checks: list[dict[str, Any]] = []

  def add_check(
    identifier: str, dimension: str, title: str, proof: dict[str, Any], atoms: set[str]
  ) -> None:
    references: list[dict[str, Any]] = []
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
  for index, evaluation in enumerate(report.get("grounding_checks", [])):
    atoms = {evaluation["judge_atom"], evaluation["context_atom"]}
    add_check(
      f"grounding:{index}",
      "grounding",
      "Compare judge finding with independent grounding",
      evaluation,
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
