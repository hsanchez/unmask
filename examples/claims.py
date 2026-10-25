"""Compile manually reviewed claims; no extraction or provider calls."""

import json

import unmask
from unmask.claims import (
  Claim,
  ClaimCase,
  CompiledClaims,
  InventoryReview,
  Message,
  Relation,
  RelationReview,
)
from unmask.judge import Evidence, Truth

context = "Alice is CTO."
response = "Alice is CEO."
rationale = "The context supports Alice being CEO. PASS."
context_span = Evidence("context", 0, len(context), context)
response_span = Evidence("response", 0, len(response), response)
rationale_span = Evidence("rationale", 0, len(rationale), rationale)

case = ClaimCase(
  id="role_review",
  context=context,
  response=response,
  rationale=rationale,
  verdict="PASS",
  messages=(
    Message("context_msg", context_span),
    Message("response_msg", response_span),
    Message("judge_msg", rationale_span),
  ),
  claims=(Claim("role", "response_msg", response_span),),
  reviews=(
    RelationReview(
      "address",
      "role",
      ("judge_msg",),
      Relation.ADDRESSES,
      Truth.TRUE,
      (rationale_span,),
      "Judge explicitly addresses Alice's role.",
    ),
    RelationReview(
      "judge",
      "role",
      ("judge_msg",),
      Relation.JUDGES_SUPPORTED,
      Truth.TRUE,
      (rationale_span,),
      "Judge states the claim is supported.",
    ),
    RelationReview(
      "support",
      "role",
      ("context_msg",),
      Relation.SUPPORTS,
      Truth.FALSE,
      (context_span,),
      "Independent review finds no support for CEO in the complete context.",
    ),
  ),
  inventory=InventoryReview(
    ("response_msg",),
    Truth.TRUE,
    (response_span,),
    "Reviewed entire response: one factual claim.",
  ),
)
compiled = case.compile()
restored = CompiledClaims.from_json(compiled.to_json())
result = unmask.adherence(restored.case)
print("Synthetic, manually reviewed claims; semantic truth is supplied.")
print(result.overall)
for finding in result.findings:
  print(finding.kind, [claim.id for claim in restored.claims_for(finding)])
for gap in restored.review_gaps:
  print("Needs review:", gap.subject, gap.summary)

# Ordinary replay JSON remains accepted by the existing CLI and studio.
replay_json = json.dumps(restored.case.to_dict(), ensure_ascii=False)
