"""Typed claim lowering, finite completeness, and provenance contracts."""

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from tests.helpers import load_json_object
from unmask import adherence
from unmask.benchmarks import Adherence, BenchmarkCase
from unmask.claims import (
  Claim,
  ClaimCase,
  CompiledClaims,
  InventoryReview,
  Message,
  Relation,
  RelationReview,
)
from unmask.judge import (
  Annotation,
  Atom,
  Evidence,
  Expr,
  ProtocolRule,
  Truth,
  ValidationError,
  VerificationCase,
)


@pytest.fixture
def claim_case() -> ClaimCase:
  context = "Zoë is CTO."
  response = "Zoë is CTO."
  rationale = "The supplied context supports the claim. PASS."
  context_span = Evidence("context", 0, len(context), context)
  response_span = Evidence("response", 0, len(response), response)
  rationale_span = Evidence("rationale", 0, len(rationale), rationale)
  return ClaimCase(
    id="review",
    context=context,
    response=response,
    rationale=rationale,
    verdict="PASS",
    messages=(
      Message("context_msg", context_span),
      Message("response_msg", response_span),
      Message("judge_msg", rationale_span),
    ),
    claims=(Claim("c1", "response_msg", response_span),),
    reviews=(
      RelationReview(
        "address",
        "c1",
        ("judge_msg",),
        Relation.ADDRESSES,
        Truth.TRUE,
        (rationale_span,),
        "Judge explicitly addresses the claim.",
      ),
      RelationReview(
        "judge",
        "c1",
        ("judge_msg",),
        Relation.JUDGES_SUPPORTED,
        Truth.TRUE,
        (rationale_span,),
        "Judge labels the claim supported.",
      ),
      RelationReview(
        "support",
        "c1",
        ("context_msg",),
        Relation.SUPPORTS,
        Truth.TRUE,
        (context_span,),
        "Independent review finds support.",
      ),
    ),
    inventory=InventoryReview(
      ("response_msg",),
      Truth.TRUE,
      (response_span,),
      "The whole response contains this one claim.",
    ),
  )


def test_supported_claim_is_adherent(claim_case: ClaimCase) -> None:
  result = adherence(claim_case.compile().case)
  assert result.overall == "SATISFIED"
  assert not result.findings
  assert not claim_case.compile().review_gaps


@pytest.mark.parametrize(
  "relation", [Relation.ADDRESSES, Relation.JUDGES_SUPPORTED, Relation.SUPPORTS]
)
@pytest.mark.parametrize("value", [None, Truth.UNKNOWN, Truth.FALSE])
def test_review_gaps_distinguish_missing_unknown_and_negative(
  claim_case: ClaimCase, relation: Relation, value: Truth | None
) -> None:
  reviews = tuple(
    replace(review, value=value)
    if review.relation == relation and value is not None
    else review
    for review in claim_case.reviews
    if review.relation != relation or value is not None
  )
  gaps = replace(claim_case, reviews=reviews).compile().review_gaps
  if value == Truth.FALSE:
    assert not gaps
  else:
    assert len(gaps) == 1
    assert gaps[0].claim == claim_case.claims[0]
    assert ("unresolved" if value else "not been supplied") in gaps[0].summary


@pytest.mark.parametrize("value", [Truth.TRUE, Truth.FALSE, Truth.UNKNOWN])
def test_contradiction_review_gap_requires_positive_contradiction(
  claim_case: ClaimCase, value: Truth
) -> None:
  review = replace(claim_case.reviews[2], relation=Relation.CONTRADICTS, value=value)
  compiled = replace(claim_case, reviews=(*claim_case.reviews[:2], review)).compile()
  assert len(compiled.review_gaps) == (0 if value == Truth.TRUE else 1)


@pytest.mark.parametrize("value", [None, Truth.UNKNOWN, Truth.FALSE])
def test_inventory_gap_does_not_invent_missing_claims(
  claim_case: ClaimCase, value: Truth | None
) -> None:
  assert claim_case.inventory is not None
  inventory = replace(claim_case.inventory, value=value) if value else None
  gaps = replace(claim_case, inventory=inventory).compile().review_gaps
  assert len(gaps) == 1
  assert gaps[0].subject == "inventory_complete"
  assert gaps[0].claim is None


def test_review_gap_coexists_with_grounding_violation(claim_case: ClaimCase) -> None:
  compiled = replace(
    claim_case,
    reviews=(claim_case.reviews[1], replace(claim_case.reviews[2], value=Truth.FALSE)),
  ).compile()
  assert adherence(compiled.case).overall == "VIOLATED"
  assert len(compiled.review_gaps) == 1
  assert compiled.review_gaps[0].subject == compiled.bindings[0].addressed_atom


def test_review_gaps_are_stable_under_record_reordering(claim_case: ClaimCase) -> None:
  case = replace(claim_case, reviews=(), inventory=None)
  reordered = replace(case, messages=case.messages[::-1])
  assert case.compile().review_gaps == reordered.compile().review_gaps


def test_incorrect_judge_support_localizes_the_claim(claim_case: ClaimCase) -> None:
  reviews = (*claim_case.reviews[:2], replace(claim_case.reviews[2], value=Truth.FALSE))
  compiled = replace(claim_case, reviews=reviews).compile()
  result = adherence(compiled.case)
  assert result.overall == "VIOLATED"
  finding = next(finding for finding in result.findings if finding.kind == "grounding")
  assert compiled.claims_for(finding) == claim_case.claims


def test_correct_fail_is_adherent(claim_case: ClaimCase) -> None:
  reviews = tuple(
    replace(review, value=Truth.FALSE)
    if review.relation != Relation.ADDRESSES
    else review
    for review in claim_case.reviews
  )
  result = adherence(
    replace(claim_case, verdict="FAIL", reviews=reviews).compile().case
  )
  assert result.overall == "SATISFIED"


@pytest.mark.parametrize(
  "missing", [Relation.ADDRESSES, Relation.JUDGES_SUPPORTED, Relation.SUPPORTS]
)
def test_missing_relations_remain_unknown(
  claim_case: ClaimCase, missing: Relation
) -> None:
  compiled = replace(
    claim_case,
    reviews=tuple(
      review for review in claim_case.reviews if review.relation != missing
    ),
  ).compile()
  result = adherence(compiled.case)
  assert result.overall == "UNKNOWN"
  assert result.unknown_atoms


@pytest.mark.parametrize(
  "value,expected",
  [(Truth.TRUE, "VIOLATED"), (Truth.FALSE, "UNKNOWN"), (Truth.UNKNOWN, "UNKNOWN")],
)
def test_contradiction_is_not_the_inverse_of_support(
  claim_case: ClaimCase, value: Truth, expected: str
) -> None:
  review = replace(claim_case.reviews[2], relation=Relation.CONTRADICTS, value=value)
  result = adherence(
    replace(claim_case, reviews=(*claim_case.reviews[:2], review)).compile().case
  )
  assert result.overall == expected


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_reviews_are_inconsistent(
  claim_case: ClaimCase, reverse: bool
) -> None:
  conflict = replace(claim_case.reviews[2], id="conflict", value=Truth.FALSE)
  reviews = (*claim_case.reviews, conflict)
  compiled = replace(
    claim_case, reviews=reviews[::-1] if reverse else reviews
  ).compile()
  result = adherence(compiled.case)
  assert result.status.startswith("INCONSISTENT_")
  assert result.overall != "SATISFIED"
  finding = result.findings[-1]
  assert compiled.claims_for(finding) == claim_case.claims
  assert {annotation.value for annotation in finding.conflicting_annotations} == {
    Truth.TRUE,
    Truth.FALSE,
  }
  assert all(annotation.evidence for annotation in finding.conflicting_annotations)
  assert not compiled.review_gaps


def test_conflict_localization_excludes_unrelated_claims(claim_case: ClaimCase) -> None:
  second = replace(claim_case.claims[0], id="c2")
  conflict = replace(claim_case.reviews[2], id="conflict", value=Truth.FALSE)
  compiled = replace(
    claim_case,
    claims=(*claim_case.claims, second),
    reviews=(*claim_case.reviews, conflict),
  ).compile()
  finding = adherence(compiled.case).findings[-1]
  assert compiled.claims_for(finding) == claim_case.claims


def test_conflict_between_claims_localizes_both(claim_case: ClaimCase) -> None:
  second = replace(claim_case.claims[0], id="c2")
  reviews = tuple(
    replace(review, id=f"second_{review.id}", claim="c2")
    for review in claim_case.reviews
  )
  compiled = replace(
    claim_case,
    claims=(*claim_case.claims, second),
    reviews=(*claim_case.reviews, *reviews),
  ).compile()
  constraint = ProtocolRule(
    "exclusive",
    "Both claims cannot be supported",
    Expr(
      "not",
      args=(
        Expr(
          "and",
          args=tuple(
            Expr("atom", atom=binding.judge_atom) for binding in compiled.bindings
          ),
        ),
      ),
    ),
  )
  result = adherence(replace(compiled.case, constraints=(constraint,)))
  assert result.status == "INCONSISTENT_BASE"
  assert compiled.claims_for(result.findings[-1]) == compiled.source.claims


def test_global_protocol_conflict_has_no_claim_attribution(
  claim_case: ClaimCase,
) -> None:
  compiled = claim_case.compile()
  constraint = ProtocolRule(
    "impossible", "Impossible protocol", Expr("const", value=False)
  )
  result = adherence(replace(compiled.case, constraints=(constraint,)))
  assert result.status == "INCONSISTENT_PROTOCOL"
  assert not result.findings[-1].conflicting_annotations
  assert compiled.claims_for(result.findings[-1]) == ()


def test_protocol_inconsistency_takes_precedence_over_review_conflicts(
  claim_case: ClaimCase,
) -> None:
  conflict = replace(claim_case.reviews[2], id="conflict", value=Truth.FALSE)
  compiled = replace(claim_case, reviews=(*claim_case.reviews, conflict)).compile()
  constraint = ProtocolRule("impossible", "Impossible", Expr("const", value=False))
  result = adherence(replace(compiled.case, constraints=(constraint,)))
  assert result.status == "INCONSISTENT_PROTOCOL"
  assert not result.findings[-1].conflicting_annotations


@pytest.mark.parametrize("inventory", [None, Truth.UNKNOWN, Truth.FALSE])
def test_inventory_review_is_required(
  claim_case: ClaimCase, inventory: Truth | None
) -> None:
  original = claim_case.inventory
  assert original is not None
  reviewed = replace(original, value=inventory) if inventory else None
  result = adherence(replace(claim_case, inventory=reviewed).compile().case)
  assert result.overall == ("VIOLATED" if inventory == Truth.FALSE else "UNKNOWN")


def test_empty_inventory_does_not_prove_adherence(claim_case: ClaimCase) -> None:
  result = adherence(replace(claim_case, claims=(), reviews=()).compile().case)
  assert result.overall == "UNKNOWN"


@pytest.mark.parametrize("empty", [False, True])
def test_unreviewed_inventory_cannot_refute_fail(
  claim_case: ClaimCase, empty: bool
) -> None:
  case = replace(
    claim_case,
    verdict="FAIL",
    inventory=None,
    claims=() if empty else claim_case.claims,
    reviews=() if empty else claim_case.reviews,
  )
  result = adherence(case.compile().case)
  assert result.overall == "UNKNOWN"
  assert not result.findings


def test_counterexample_survives_unknown_inventory(claim_case: ClaimCase) -> None:
  reviews = (*claim_case.reviews[:2], replace(claim_case.reviews[2], value=Truth.FALSE))
  result = adherence(
    replace(claim_case, reviews=reviews, inventory=None).compile().case
  )
  assert result.overall == "VIOLATED"


@pytest.mark.parametrize("relation", [Relation.SUPPORTS, Relation.ADDRESSES])
def test_review_roles_are_validated(claim_case: ClaimCase, relation: Relation) -> None:
  review = replace(
    claim_case.reviews[0],
    relation=relation,
    messages=("response_msg",),
    evidence=(claim_case.claims[0].span,),
  )
  with pytest.raises(ValidationError, match="role"):
    _ = replace(claim_case, reviews=(review,))


def test_partial_inventory_cannot_claim_completeness(claim_case: ClaimCase) -> None:
  span = Evidence("response", 0, 3, "Zoë")
  with pytest.raises(ValidationError, match="entire response"):
    _ = replace(
      claim_case,
      messages=tuple(
        message for message in claim_case.messages if message.id != "response_msg"
      )
      + (Message("response_msg", span),),
      claims=(replace(claim_case.claims[0], span=span),),
      inventory=InventoryReview(
        ("response_msg",), Truth.TRUE, (span,), "Partial review."
      ),
    )


def test_partial_context_cannot_establish_no_support(claim_case: ClaimCase) -> None:
  span = Evidence("context", 0, 3, "Zoë")
  messages = (Message("context_msg", span), *claim_case.messages[1:])
  review = replace(claim_case.reviews[2], value=Truth.FALSE, evidence=(span,))
  with pytest.raises(ValidationError, match="complete context"):
    _ = replace(
      claim_case, messages=messages, reviews=(*claim_case.reviews[:2], review)
    )


@pytest.mark.parametrize("quote", ["Zoe\u0308", "Zoë!"])
def test_quotes_are_exact_unicode_codepoints(claim_case: ClaimCase, quote: str) -> None:
  with pytest.raises(ValidationError, match="quote mismatch"):
    _ = replace(
      claim_case,
      claims=(replace(claim_case.claims[0], span=Evidence("response", 0, 3, quote)),),
    )


def test_duplicate_entity_identifiers_are_rejected(claim_case: ClaimCase) -> None:
  with pytest.raises(ValidationError, match="duplicate"):
    _ = replace(claim_case, claims=(replace(claim_case.claims[0], id="judge_msg"),))


def test_dangling_relationship_is_rejected(claim_case: ClaimCase) -> None:
  with pytest.raises(ValidationError, match="dangling"):
    _ = replace(claim_case, reviews=(replace(claim_case.reviews[0], claim="absent"),))


def test_known_reviews_require_evidence(claim_case: ClaimCase) -> None:
  with pytest.raises(ValidationError, match="require evidence"):
    _ = replace(claim_case, reviews=(replace(claim_case.reviews[0], evidence=()),))


def test_claim_expansion_is_bounded_before_compilation(claim_case: ClaimCase) -> None:
  claims = tuple(replace(claim_case.claims[0], id=f"c{number}") for number in range(43))
  with pytest.raises(ValidationError, match="expanded atom"):
    _ = replace(claim_case, claims=claims)


def test_largest_claim_inventory_compiles(claim_case: ClaimCase) -> None:
  claims = tuple(replace(claim_case.claims[0], id=f"c{number}") for number in range(42))
  assert len(replace(claim_case, claims=claims, reviews=()).compile().case.atoms) == 127


def test_reordering_records_preserves_compilation(claim_case: ClaimCase) -> None:
  compiled = claim_case.compile()
  reordered = replace(
    claim_case,
    messages=tuple(reversed(claim_case.messages)),
    reviews=tuple(reversed(claim_case.reviews)),
  ).compile()
  assert compiled.case.to_dict() == reordered.case.to_dict()
  assert compiled.bindings == reordered.bindings


def test_added_claim_preserves_existing_identifiers(claim_case: ClaimCase) -> None:
  first = claim_case.compile().bindings[0]
  larger = replace(
    claim_case, claims=(*claim_case.claims, replace(claim_case.claims[0], id="c2"))
  ).compile()
  assert larger.bindings[0] == first


@pytest.mark.parametrize("complete", [True, False])
def test_typed_artifact_round_trip(claim_case: ClaimCase, complete: bool) -> None:
  if not complete:
    claim_case = replace(claim_case, inventory=None)
  compiled = claim_case.compile()
  restored = CompiledClaims.from_json(compiled.to_json())
  assert restored == compiled
  assert restored.case.to_dict()["schema_version"] == 1


def test_frozen_claims_v1_interpretation() -> None:
  fixture = load_json_object(
    (Path(__file__).resolve().parents[1] / "data/fixtures/claims-v1.json").read_text(
      encoding="utf-8"
    )
  )
  compiled = CompiledClaims.from_json(json.dumps(fixture["artifact"]))
  assert load_json_object(compiled.to_json()) == fixture["artifact"]
  assert compiled.case.to_dict() == fixture["replay"]
  result = adherence(compiled.case)
  assert result.overall == "VIOLATED"
  assert [finding.kind for finding in result.findings] == ["grounding"]


@pytest.mark.parametrize("version", [True, 2, "1"])
def test_typed_artifact_rejects_unknown_versions(
  claim_case: ClaimCase, version: object
) -> None:
  payload = load_json_object(claim_case.compile().to_json())
  payload["schema_version"] = version
  with pytest.raises(ValidationError, match="version"):
    _ = CompiledClaims.from_json(json.dumps(payload))


def test_typed_artifact_rejects_modified_provenance(claim_case: ClaimCase) -> None:
  payload = load_json_object(claim_case.compile().to_json())
  payload["bindings"] = []
  with pytest.raises(ValidationError, match="binding mismatch"):
    _ = CompiledClaims.from_json(json.dumps(payload))


def test_invalid_unicode_in_provenance_is_rejected(claim_case: ClaimCase) -> None:
  payload = load_json_object(claim_case.compile().to_json())
  payload["bindings"] = [{"claim": "\ud800"}]
  with pytest.raises(ValidationError, match="binding mismatch"):
    _ = CompiledClaims.from_json(json.dumps(payload))


def test_typed_artifact_rejects_duplicate_fields() -> None:
  with pytest.raises(ValidationError, match="duplicate"):
    _ = CompiledClaims.from_json('{"schema_version":1,"schema_version":1}')


def test_typed_sources_are_byte_bounded(claim_case: ClaimCase) -> None:
  with pytest.raises(ValidationError, match="byte limit"):
    _ = replace(claim_case, context="é" * 500_001)


@pytest.mark.parametrize("support", [Truth.TRUE, Truth.FALSE])
def test_manual_replay_has_equivalent_checks(
  claim_case: ClaimCase, support: Truth
) -> None:
  claim_case = replace(
    claim_case,
    reviews=(*claim_case.reviews[:2], replace(claim_case.reviews[2], value=support)),
  )
  compiled = claim_case.compile().case
  manual = VerificationCase(
    id="manual",
    context=claim_case.context,
    response=claim_case.response,
    rationale=claim_case.rationale,
    verdict="PASS",
    protocol_text="Manually reviewed protocol.",
    atoms=(
      Atom("inventory_complete", "context", "Complete inventory"),
      Atom("judge_c1", "judge", "Judge support"),
      Atom("support_c1", "context", "Context support"),
      Atom("addressed_c1", "procedure", "Addressed"),
    ),
    decision=Expr("and", (Expr("atom", atom="judge_c1"),)),
    requirements=(
      ProtocolRule(
        "inventory_complete",
        "Inventory",
        Expr("atom", atom="inventory_complete"),
        Expr("atom", atom="inventory_complete"),
      ),
      ProtocolRule(
        "claim:c1:addressed",
        "Addressed",
        Expr("atom", atom="addressed_c1"),
        Expr("atom", atom="addressed_c1"),
      ),
    ),
    annotations=(
      Annotation(
        "inventory_complete",
        Truth.TRUE,
        (claim_case.claims[0].span,),
        "Reviewed inventory",
      ),
      *tuple(
        Annotation(atom, review.value, review.evidence, review.explanation)
        for atom, review in zip(
          ("addressed_c1", "judge_c1", "support_c1"), claim_case.reviews, strict=True
        )
      ),
    ),
    grounding_pairs=(("judge_c1", "support_c1"),),
    extraction={"provider": "manual", "translation_status": "VALID"},
  )
  manual_result = adherence(manual)
  compiled_result = adherence(compiled)
  assert [(check.id, check.status) for check in manual_result.checks] == [
    (check.id, check.status) for check in compiled_result.checks
  ]
  assert [(finding.id, finding.kind) for finding in manual_result.findings] == [
    (finding.id, finding.kind) for finding in compiled_result.findings
  ]


@pytest.mark.parametrize(
  "text", ['{"schema_version":NaN}', "[]", "{", '{"x":' + "9" * 5000 + "}"]
)
def test_malformed_artifacts_raise_validation_error(text: str) -> None:
  with pytest.raises(ValidationError):
    _ = CompiledClaims.from_json(text)


@pytest.mark.parametrize(
  "field,value", [("value", "yes"), ("relation", "entails"), ("value", False)]
)
def test_artifact_enums_are_validated(
  claim_case: ClaimCase, field: str, value: object
) -> None:
  payload = load_json_object(claim_case.compile().to_json())
  raw = cast("dict[str, object]", payload["input"])
  reviews = cast("list[dict[str, object]]", raw["reviews"])
  reviews[0][field] = value
  with pytest.raises(ValidationError):
    _ = CompiledClaims.from_json(json.dumps(payload))


def test_evidence_cannot_escape_declared_message(claim_case: ClaimCase) -> None:
  partial = Message("partial", Evidence("rationale", 0, 3, "The"))
  review = replace(claim_case.reviews[0], messages=("partial",))
  with pytest.raises(ValidationError, match="outside message scope"):
    _ = replace(claim_case, messages=(*claim_case.messages, partial), reviews=(review,))


def test_expanded_quotes_are_byte_bounded(claim_case: ClaimCase) -> None:
  text = "x" * 300_000
  span = Evidence("context", 0, len(text), text)
  support = replace(claim_case.reviews[2], evidence=(span, span, span))
  with pytest.raises(ValidationError, match="byte limit"):
    _ = replace(
      claim_case,
      context=text,
      messages=(Message("context_msg", span), *claim_case.messages[1:]),
      reviews=(*claim_case.reviews[:2], support),
    )


def test_explicit_unaddressed_claim_is_a_violation(claim_case: ClaimCase) -> None:
  review = replace(claim_case.reviews[0], value=Truth.FALSE)
  result = adherence(
    replace(claim_case, reviews=(review, *claim_case.reviews[1:])).compile().case
  )
  assert result.overall == "VIOLATED"


def test_compiled_case_reuses_existing_adherence_benchmark(
  claim_case: ClaimCase,
) -> None:
  inputs = claim_case.compile().case.to_dict()
  _ = inputs.pop("gold_verdict", None)
  benchmark = Adherence(
    name="typed_fixture",
    dataset_revision="synthetic-v1",
    load=lambda: (
      BenchmarkCase(
        "c1",
        "claim-review",
        inputs,
        {"overall": "SATISFIED", "findings": [], "review_status": "synthetic"},
      ),
    ),
  )
  run = benchmark.evaluate(
    model=adherence, target_name="unmask", target_revision="test"
  )
  assert run.counts["scored"] == 1
  assert run.metrics[0].value == 1.0


def test_validation_error_identifies_the_bad_claim(claim_case: ClaimCase) -> None:
  invalid = replace(claim_case.claims[0], span=Evidence("response", 0, 3, "Who"))
  with pytest.raises(
    ValidationError, match=r"claim 'c1': evidence quote mismatch at response\[0:3\]"
  ):
    _ = replace(claim_case, claims=(invalid,))


def test_enum_error_is_contextual_without_reflecting_input(
  claim_case: ClaimCase,
) -> None:
  payload = load_json_object(claim_case.compile().to_json())
  raw = cast("dict[str, object]", payload["input"])
  reviews = cast("list[dict[str, object]]", raw["reviews"])
  reviews[0]["relation"] = "\n\x1b[31mFORGED" * 1000
  with pytest.raises(ValidationError, match=r"reviews\[0\]: invalid Relation") as error:
    _ = CompiledClaims.from_json(json.dumps(payload))
  assert len(str(error.value)) < 100
  assert "FORGED" not in str(error.value)


def test_incomplete_inventory_can_use_the_full_atom_budget(
  claim_case: ClaimCase,
) -> None:
  claims = tuple(replace(claim_case.claims[0], id=f"c{number}") for number in range(42))
  compiled = replace(claim_case, claims=claims, reviews=(), inventory=None).compile()
  assert len(compiled.case.atoms) == 128


@pytest.mark.parametrize("dimension", ["procedure", "coverage"])
def test_unaddressed_finding_maps_to_its_claim(
  claim_case: ClaimCase, dimension: str
) -> None:
  reviews = (replace(claim_case.reviews[0], value=Truth.FALSE), *claim_case.reviews[1:])
  compiled = replace(claim_case, reviews=reviews).compile()
  finding = next(
    item for item in adherence(compiled.case).findings if item.kind == dimension
  )
  assert compiled.claims_for(finding) == claim_case.claims


def test_inventory_findings_have_no_claim_mapping(claim_case: ClaimCase) -> None:
  inventory = claim_case.inventory
  assert inventory is not None
  compiled = replace(
    claim_case, inventory=replace(inventory, value=Truth.FALSE)
  ).compile()
  result = adherence(compiled.case)
  assert result.findings
  assert all(not compiled.claims_for(finding) for finding in result.findings)


def test_negative_addressing_requires_the_complete_rationale(
  claim_case: ClaimCase,
) -> None:
  span = Evidence("rationale", 0, 3, "The")
  partial = Message("partial", span)
  review = replace(
    claim_case.reviews[0], messages=("partial",), evidence=(span,), value=Truth.FALSE
  )
  with pytest.raises(
    ValidationError, match="negative addressing requires complete rationale"
  ):
    _ = replace(claim_case, messages=(*claim_case.messages, partial), reviews=(review,))


def test_large_aliases_are_rejected_before_later_span_checks(
  claim_case: ClaimCase,
) -> None:
  text = "x" * 300_000
  span = Evidence("context", 0, len(text), text)
  reviews = (
    replace(claim_case.reviews[2], evidence=(span,) * 16),
    replace(
      claim_case.reviews[2], id="invalid", evidence=(replace(span, quote="wrong"),)
    ),
  )
  with pytest.raises(ValidationError, match="byte limit"):
    _ = replace(
      claim_case,
      context=text,
      messages=(Message("context_msg", span), *claim_case.messages[1:]),
      reviews=reviews,
    )


@pytest.mark.parametrize("text", ["x" * 350_000, "é" * 175_000])
def test_repeated_explanations_exhaust_budget_before_later_reviews(
  claim_case: ClaimCase, text: str
) -> None:
  reviews = tuple(
    replace(claim_case.reviews[0], id=f"review{index}", explanation=text)
    for index in range(3)
  )
  invalid = replace(claim_case.reviews[0], id="invalid", claim="missing")
  with pytest.raises(
    ValidationError, match="review 'review2': typed artifact exceeds byte limit"
  ):
    _ = replace(claim_case, reviews=(*reviews, invalid))


@pytest.mark.parametrize("field", ["source", "quote", "explanation", "scope", "id"])
def test_surrogates_in_artifact_strings_raise_validation_error(
  claim_case: ClaimCase, field: str
) -> None:
  payload = load_json_object(claim_case.compile().to_json())
  raw = cast("dict[str, object]", payload["input"])
  reviews = cast("list[dict[str, object]]", raw["reviews"])
  if field == "source":
    raw["context"] = "\ud800"
  elif field == "scope":
    reviews[0]["messages"] = ["\ud800"]
  elif field == "quote":
    evidence = cast("list[dict[str, object]]", reviews[0]["evidence"])
    evidence[0]["quote"] = "\ud800"
  else:
    reviews[0][field] = "\ud800"
  with pytest.raises(ValidationError):
    _ = CompiledClaims.from_json(json.dumps(payload))


def test_compilation_records_the_caller_assertion_boundary(
  claim_case: ClaimCase,
) -> None:
  extraction = claim_case.compile().case.extraction
  assert extraction["provider"] == "caller-supplied-claim-compiler"
  assert "unauthenticated caller assertions" in extraction["notes"]
