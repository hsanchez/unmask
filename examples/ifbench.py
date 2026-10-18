"""Benchmark unmask.adherence on manually annotated, IFBench-shaped JSON cases."""

from typing import cast

import unmask
from unmask.benches import Adherence
from unmask.benchmarks import Benchmark, BenchmarkCase


def load_cases() -> tuple[BenchmarkCase, ...]:
  cases: list[BenchmarkCase] = []
  for identifier, response, rationale, expected, response_is_json, observed in (
    (
      "supported",
      '{"greeting":"hello"}',
      "The response is a JSON object. PASS.",
      "SATISFIED",
      True,
      True,
    ),
    (
      "incorrect-judge",
      "hello",
      "The response is a JSON object. PASS.",
      "VIOLATED",
      False,
      True,
    ),
    ("insufficient-evidence", '{"greeting":"hello"}', "PASS.", "UNKNOWN", True, False),
  ):
    rationale_evidence = (unmask.Evidence("rationale", 0, len(rationale), rationale),)
    annotations = [
      unmask.Annotation(
        "context_json",
        unmask.Truth.TRUE if response_is_json else unmask.Truth.FALSE,
        (unmask.Evidence("response", 0, len(response), response),),
        "Manually assessed JSON-object validity of this fixed response.",
      ),
    ]
    if observed:
      annotations.extend(
        (
          unmask.Annotation(
            "judge_json",
            unmask.Truth.TRUE,
            rationale_evidence,
            "Judge explicitly claims JSON validity.",
          ),
          unmask.Annotation(
            "examined",
            unmask.Truth.TRUE,
            rationale_evidence,
            "Judge explicitly assesses the response.",
          ),
        )
      )
    examined = unmask.Expr("atom", atom="examined")
    case = unmask.VerificationCase(
      id=identifier,
      protocol_text="Assess the response to 'Return a JSON object with a greeting.' Explicitly check JSON-object validity. PASS iff it is a JSON object.",
      context="The expected response format is a JSON object.",
      response=response,
      rationale=rationale,
      verdict="PASS",
      atoms=(
        unmask.Atom("judge_json", "judge", "Judge claims response is a JSON object"),
        unmask.Atom(
          "context_json", "context", "Independent assessment: response is a JSON object"
        ),
        unmask.Atom("examined", "procedure", "Judge explicitly assesses JSON validity"),
      ),
      decision=unmask.Expr("atom", atom="judge_json"),
      requirements=(
        unmask.ProtocolRule(
          "check_json", "Explicitly assess JSON validity", examined, examined
        ),
      ),
      annotations=tuple(annotations),
      grounding_pairs=(("judge_json", "context_json"),),
      extraction={
        "provider": "manual-synthetic-fixture",
        "translation_status": "VALID",
      },
    )
    inputs = case.to_dict()
    _ = inputs.pop("gold_verdict", None)
    expected_findings: list[dict[str, object]] = []
    if identifier == "incorrect-judge":
      expected_findings.append(
        {"kind": "grounding", "subject": ["context_json", "judge_json"]}
      )
    cases.append(
      BenchmarkCase(
        identifier,
        "adherence",
        inputs,
        {
          "overall": expected,
          "findings": expected_findings,
          "review_status": "synthetic",
        },
      )
    )
  return tuple(cases)


def adherence_benchmark() -> Benchmark[
  unmask.VerificationCase, unmask.VerificationResult
]:
  return Adherence(
    name="ifbench-shaped-adherence-example",
    dataset_revision="manual-json-evaluations/v2",
    load=load_cases,
  )


def main() -> None:
  run = adherence_benchmark().evaluate(
    model=unmask.adherence,
    target_name="unmask.adherence",
    target_revision="default-timeout-1000ms/v1",
  )
  print("Target: unmask.adherence")
  print("Manually annotated synthetic cases; not an official IFBench score.")
  print("Reference review: synthetic; independent ground-truth review pending.")
  expected = {case.id: case.reference["overall"] for case in run.frozen.cases}
  for result in run.results:
    output = result.output
    if output is None:
      print(f"{result.case_id}: {result.score.outcome} — {result.score.reason}")
      continue
    summary = cast("dict[str, object]", output["summary"])
    print(
      f"{result.case_id}: {summary['overall']} / {result.score.outcome} (expected: {expected[result.case_id]})"
    )
    print(f"  Detected findings: {output['detected_findings']}")
    if result.score.reason:
      print(f"  {result.score.reason}")
  print("Joint outcome/finding agreement and coverage:", run.metrics)
  print("Outcomes:", run.counts)
  print(
    "UNKNOWN cases are excluded from agreement; annotations are supplied, not inferred."
  )


if __name__ == "__main__":
  main()
