"""Benchmark annotated protocol evaluations with Unmask's adherence result contract."""

import json
from collections.abc import Callable, Iterable
from typing import cast

from ..benchmarks import (
  Benchmark,
  BenchmarkCase,
  BenchmarkKind,
  CaseResult,
  Metric,
  Outcome,
  Score,
  mean_score,
  require_fields,
  require_items,
  require_object,
  require_text,
)
from ..judge import VerificationCase, VerificationResult


def decode_replay(inputs: dict[str, object]) -> VerificationCase:
  """Validate the replay input contract before executing any target.

  Raises:
    ValueError: if reference labels or invalid replay data appear in inputs.
  """
  if "gold_verdict" in inputs:
    raise ValueError("gold verdict belongs in the reference, never target inputs")
  return VerificationCase.from_json(json.dumps(inputs))


def encode_audit(result: VerificationResult) -> dict[str, object]:
  findings: list[dict[str, object]] = []
  for finding in result.findings:
    if finding.kind == "grounding" and finding.check is not None:
      subject = sorted(finding.check.atoms)
    elif finding.kind in ("procedure", "coverage"):
      subject = [finding.id.removeprefix(f"{finding.kind}:")]
    else:
      subject = [finding.id]
    findings.append({"kind": finding.kind, "subject": subject})
  return {**result.to_dict(), "detected_findings": findings}


type _FindingKey = tuple[str, tuple[str, ...]]


def _finding_keys(value: object) -> set[_FindingKey]:
  keys: set[_FindingKey] = set()
  for raw in require_items(value):
    finding = require_object(raw)
    require_fields(finding, {"kind", "subject"})
    kind = require_text(finding["kind"], "finding kind")
    subject = tuple(
      require_text(item, "finding subject")
      for item in require_items(finding["subject"])
    )
    if not subject or len(subject) != len(set(subject)):
      raise ValueError("finding subject must contain distinct, non-empty identifiers")
    key = (kind, tuple(sorted(subject)))
    if key in keys:
      raise ValueError("duplicate finding key")
    keys.add(key)
  return keys


def _reference(reference: dict[str, object]) -> tuple[str, set[_FindingKey]]:
  require_fields(reference, {"overall", "findings", "review_status"})
  expected = require_text(reference["overall"], "expected overall")
  if expected not in ("SATISFIED", "VIOLATED", "UNKNOWN"):
    raise ValueError("reference must specify SATISFIED, VIOLATED, or UNKNOWN")
  if reference["review_status"] not in (
    "synthetic",
    "unreviewed",
    "independently-reviewed",
  ):
    raise ValueError("reference requires an explicit review_status")
  return expected, _finding_keys(reference["findings"])


def _describe(keys: set[_FindingKey]) -> str:
  return json.dumps(
    [{"kind": kind, "subject": list(subject)} for kind, subject in sorted(keys)],
    sort_keys=True,
  )


def score_adherence(
  inputs: dict[str, object], reference: dict[str, object], output: dict[str, object]
) -> Score:
  """Require agreement on overall status and the complete expected finding set.

  Raises:
    ValueError: if labels or the output summary are invalid.
  """
  del inputs
  expected, expected_findings = _reference(reference)
  actual_findings = _finding_keys(output.get("detected_findings"))
  summary = output.get("summary")
  if not isinstance(summary, dict):
    raise ValueError("audit output requires a summary")
  overall = cast("dict[str, object]", summary).get("overall")
  differences: list[str] = []
  if overall != expected:
    differences.append(f"Overall: expected {expected}, got {overall}")
  if missing := expected_findings - actual_findings:
    differences.append(f"Missing findings: {_describe(missing)}")
  if unexpected := actual_findings - expected_findings:
    differences.append(f"Unexpected findings: {_describe(unexpected)}")
  if overall == "UNKNOWN":
    return Score(
      Outcome.UNKNOWN,
      reason="; ".join(["audit did not resolve adherence", *differences]),
    )
  if overall not in ("SATISFIED", "VIOLATED"):
    raise ValueError("invalid adherence output")
  return Score(
    Outcome.SCORED, float(not differences), str(overall), "; ".join(differences)
  )


def aggregate_adherence(results: tuple[CaseResult, ...]) -> tuple[Metric, ...]:
  """Report joint agreement on resolved cases and coverage over every case."""
  agreement = mean_score(results)[0]
  return (
    Metric("joint_agreement", agreement.value, agreement.denominator),
    Metric(
      "resolved_coverage",
      agreement.denominator / len(results) if results else None,
      len(results),
    ),
  )


def Adherence(
  *,
  name: str,
  dataset_revision: str,
  load: Callable[[], Iterable[BenchmarkCase]],
) -> Benchmark[VerificationCase, VerificationResult]:
  """Define an annotated benchmark with overall-and-finding agreement scoring.

  UNKNOWN remains unresolved and is excluded from the mean, even when expected.
  References declare review status; this API cannot authenticate that review.

  Raises:
    ValueError: if benchmark identity or loader configuration is invalid.
  """

  if not callable(cast("object", load)):
    raise ValueError("load must be callable")

  def checked_cases() -> Iterable[BenchmarkCase]:
    for case in load():
      if not isinstance(cast("object", case), BenchmarkCase):
        raise ValueError("load must yield BenchmarkCase objects")
      _ = _reference(case.reference)
      yield case

  return Benchmark(
    name=name,
    dataset_revision=dataset_revision,
    scoring_id="adherence-overall-and-findings/exact-set/mean-resolved/v3",
    kind=BenchmarkKind.JUDGE_AUDIT,
    load=checked_cases,
    decode_input=decode_replay,
    encode_output=encode_audit,
    score=score_adherence,
    aggregate=aggregate_adherence,
  )
