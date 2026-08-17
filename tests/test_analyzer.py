import pytest

from unmask.analyzer import SymbolicAnalyzer
from unmask.models import (
  Ambiguity,
  AmbiguityInterpretation,
  Guardrail,
  GuardrailRule,
  Literal,
  RuleSemantics,
  SymbolicModel,
)


def _guardrail(*rule_ids: str) -> Guardrail:
  return Guardrail(
    name="test-guardrail",
    system_prompt="system",
    rules=tuple(GuardrailRule(id=rule_id, text=rule_id) for rule_id in rule_ids),
  )


class TestSymbolicAnalyzerInit:
  def test_rejects_mismatched_rule_ids(self) -> None:
    model = SymbolicModel(
      predicates={"is_educational": "desc"},
      safe_condition=((Literal("is_educational"),),),
      rules=(
        RuleSemantics(
          rule_id="rule:2",
          decision="block",
          condition=((Literal("is_educational"),),),
        ),
      ),
    )
    with pytest.raises(ValueError, match="do not match"):
      _ = SymbolicAnalyzer(model, _guardrail("rule:1"))

  def test_rejects_ambiguity_referencing_unknown_rule(self) -> None:
    interpretation = AmbiguityInterpretation(
      label="a", decision="block", condition=((Literal("p"),),), rationale="r"
    )
    model = SymbolicModel(
      predicates={"p": "desc"},
      safe_condition=((Literal("p"),),),
      rules=(
        RuleSemantics(rule_id="rule:1", decision="block", condition=((Literal("p"),),)),
      ),
      ambiguities=(
        Ambiguity(
          rule_id="rule:unknown",
          category="cat",
          excerpt="excerpt",
          rationale="r",
          interpretation_a=interpretation,
          interpretation_b=interpretation,
        ),
      ),
    )
    with pytest.raises(ValueError, match="unknown guardrail rules"):
      _ = SymbolicAnalyzer(model, _guardrail("rule:1"))

  def test_rejects_unknown_predicate_assignment(self) -> None:
    model = SymbolicModel(
      predicates={"p": "desc"},
      safe_condition=((Literal("p"),),),
      rules=(
        RuleSemantics(rule_id="rule:1", decision="block", condition=((Literal("p"),),)),
      ),
    )
    with pytest.raises(ValueError, match="unknown predicates"):
      _ = SymbolicAnalyzer(
        model, _guardrail("rule:1"), predicate_assignments={"q": True}
      )


class TestSymbolicAnalyzerAnalyze:
  def test_finds_over_strict_rule(self) -> None:
    model = SymbolicModel(
      predicates={"is_educational": "desc", "asks_for_advice": "desc"},
      safe_condition=((Literal("is_educational"),),),
      rules=(
        RuleSemantics(
          rule_id="rule:1",
          decision="block",
          condition=((Literal("is_educational"), Literal("asks_for_advice")),),
        ),
      ),
    )
    findings = SymbolicAnalyzer(model, _guardrail("rule:1")).analyze()
    assert [finding.bug_type for finding in findings] == ["over_strict"]
    assert findings[0].witness["is_educational"] is True

  def test_no_over_strict_finding_when_exclusion_prevents_overlap(self) -> None:
    model = SymbolicModel(
      predicates={"is_educational": "desc", "asks_for_advice": "desc"},
      safe_condition=((Literal("is_educational"),),),
      rules=(
        RuleSemantics(
          rule_id="rule:1",
          decision="block",
          condition=((Literal("asks_for_advice"),),),
        ),
      ),
      exclusions=(("is_educational", "asks_for_advice"),),
    )
    findings = SymbolicAnalyzer(model, _guardrail("rule:1")).analyze()
    assert findings == []

  def test_finds_contradiction_between_allow_and_block_rules(self) -> None:
    model = SymbolicModel(
      predicates={"p": "desc"},
      safe_condition=((Literal("p", value=False),),),
      rules=(
        RuleSemantics(rule_id="rule:1", decision="allow", condition=((Literal("p"),),)),
        RuleSemantics(rule_id="rule:2", decision="block", condition=((Literal("p"),),)),
      ),
    )
    findings = SymbolicAnalyzer(model, _guardrail("rule:1", "rule:2")).analyze()
    assert [finding.bug_type for finding in findings] == ["contradiction"]
    assert findings[0].rule_ids == ("rule:1", "rule:2")

  def test_finds_ambiguity_when_interpretations_diverge(self) -> None:
    interpretation_a = AmbiguityInterpretation(
      label="any advice",
      decision="block",
      condition=((Literal("asks_for_advice"),),),
      rationale="r",
    )
    interpretation_b = AmbiguityInterpretation(
      label="high-impact only",
      decision="allow",
      condition=((Literal("asks_for_advice"), Literal("is_high_impact")),),
      rationale="r",
    )
    model = SymbolicModel(
      predicates={"asks_for_advice": "desc", "is_high_impact": "desc"},
      safe_condition=((Literal("asks_for_advice", value=False),),),
      rules=(
        RuleSemantics(
          rule_id="rule:1",
          decision="block",
          condition=((Literal("asks_for_advice"),),),
        ),
      ),
      ambiguities=(
        Ambiguity(
          rule_id="rule:1",
          category="undefined_threshold",
          excerpt="substantial",
          rationale="no threshold defined",
          interpretation_a=interpretation_a,
          interpretation_b=interpretation_b,
        ),
      ),
    )
    findings = SymbolicAnalyzer(model, _guardrail("rule:1")).analyze()
    bug_types = {finding.bug_type for finding in findings}
    assert "ambiguity" in bug_types

  def test_predicate_assignments_pin_the_solver(self) -> None:
    model = SymbolicModel(
      predicates={"is_educational": "desc"},
      safe_condition=((Literal("is_educational"),),),
      rules=(
        RuleSemantics(
          rule_id="rule:1",
          decision="block",
          condition=((Literal("is_educational"),),),
        ),
      ),
    )
    findings = SymbolicAnalyzer(
      model,
      _guardrail("rule:1"),
      predicate_assignments={"is_educational": False},
    ).analyze()
    assert findings == []
