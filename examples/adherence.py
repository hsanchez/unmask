"""Assess annotated replay cases from JSON and typed Python inputs."""

from importlib.resources import files

import unmask


def python_case() -> unmask.VerificationCase:
  context = "Alice is CTO."
  rationale = "The claim is supported by the supplied context. PASS."
  rationale_evidence = (unmask.Evidence("rationale", 0, len(rationale), rationale),)
  addressed = unmask.Expr("atom", atom="addressed")
  return unmask.VerificationCase(
    id="single-claim",
    protocol_text="Address the claim. PASS iff it is supported by the supplied context.",
    context=context,
    response="Alice is CTO.",
    rationale=rationale,
    verdict="PASS",
    atoms=(
      unmask.Atom("judge_support", "judge", "Judge states the claim is supported"),
      unmask.Atom("context_support", "context", "Context supports the claim"),
      unmask.Atom("addressed", "procedure", "Rationale addresses the claim"),
    ),
    decision=unmask.Expr("atom", atom="judge_support"),
    requirements=(
      unmask.ProtocolRule("check_claim", "Address the claim", addressed, addressed),
    ),
    annotations=(
      unmask.Annotation(
        "judge_support",
        unmask.Truth.TRUE,
        rationale_evidence,
        "The judge explicitly reports support.",
      ),
      unmask.Annotation(
        "context_support",
        unmask.Truth.TRUE,
        (unmask.Evidence("context", 0, len(context), context),),
        "The context directly states the response's claim.",
      ),
      unmask.Annotation(
        "addressed",
        unmask.Truth.TRUE,
        rationale_evidence,
        "The rationale explicitly assesses the claim.",
      ),
    ),
    grounding_pairs=(("judge_support", "context_support"),),
    extraction={"provider": "manual", "translation_status": "VALID"},
  )


def main() -> None:
  case_json = (
    files("unmask").joinpath("web/samples/judge-audit.json").read_text(encoding="utf-8")
  )
  cases = (
    ("JSON replay", unmask.VerificationCase.from_json(case_json)),
    ("Python case", python_case()),
  )
  for name, case in cases:
    result = unmask.adherence(case)
    print(f"{name}: {result.status} / {result.overall}")
    for finding in result.findings:
      print(finding.summary)
    for check in result.checks:
      print(f"  {check.dimension}: {check.status} — {check.summary}")
    print(f"  Scope: {result.limitations[0]}")


if __name__ == "__main__":
  main()
