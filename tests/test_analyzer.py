from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
import z3

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
from unmask.solver import (
  conjunction,
  disjunction,
  model_assignment,
  negation,
  require_bool_ref,
)


def test_shared_boolean_boundary_reports_the_invalid_type() -> None:
  with pytest.raises(TypeError, match="expected a Z3 Boolean expression, got str"):
    _ = require_bool_ref("not a compiled Boolean")


@pytest.mark.parametrize(
  "operation,identity", [(conjunction, True), (disjunction, False)]
)
def test_empty_boolean_operations_keep_the_default_context(
  operation: Callable[..., z3.BoolRef], identity: bool
) -> None:
  expression = operation()
  assert expression.ctx == z3.main_ctx()
  assert z3.is_true(z3.simplify(expression)) is identity


@pytest.mark.parametrize(
  "operation,identity", [(conjunction, True), (disjunction, False)]
)
def test_empty_boolean_operations_use_an_explicit_context(
  operation: Callable[..., z3.BoolRef], identity: bool
) -> None:
  context = z3.Context()
  expression = operation(context=context)
  assert expression.ctx == context
  solver = z3.Solver(ctx=context)
  solver.add(expression)
  assert solver.check() == (z3.sat if identity else z3.unsat)


@pytest.mark.parametrize("operation", [conjunction, disjunction])
def test_boolean_operations_accept_operands_in_the_explicit_context(
  operation: Callable[..., z3.BoolRef],
) -> None:
  context = z3.Context()
  symbol = z3.Bool("symbol", ctx=context)
  expression = operation(symbol, context=context)
  solver = z3.Solver(ctx=context)
  solver.add(expression, negation(symbol))
  assert expression.ctx == context
  assert solver.check() == z3.unsat


@pytest.mark.parametrize("operation", [conjunction, disjunction])
def test_boolean_operations_reject_an_incompatible_explicit_context(
  operation: Callable[..., z3.BoolRef],
) -> None:
  operand_context = z3.Context()
  selected_context = z3.Context()
  symbol = z3.Bool("symbol", ctx=operand_context)
  with pytest.raises(z3.Z3Exception, match="[Cc]ontext mismatch"):
    _ = operation(symbol, context=selected_context)


@pytest.mark.parametrize("operation", [conjunction, disjunction])
def test_boolean_operations_reject_mixed_operand_contexts(
  operation: Callable[..., z3.BoolRef],
) -> None:
  first_context = z3.Context()
  second_context = z3.Context()
  with pytest.raises(z3.Z3Exception, match="[Cc]ontext mismatch"):
    _ = operation(
      z3.Bool("first", ctx=first_context), z3.Bool("second", ctx=second_context)
    )


@pytest.mark.parametrize(
  "operation,identity", [(conjunction, True), (disjunction, False)]
)
def test_empty_boolean_operations_keep_parallel_contexts_isolated(
  operation: Callable[..., z3.BoolRef], identity: bool
) -> None:
  def solve(_: int) -> bool:
    context = z3.Context()
    symbol = z3.Bool("symbol", ctx=context)
    solver = z3.Solver(ctx=context)
    solver.add(symbol, operation(context=context))
    return str(solver.check()) == str(z3.sat)

  with ThreadPoolExecutor(max_workers=2) as executor:
    assert list(executor.map(solve, range(8))) == [identity] * 8


@pytest.mark.parametrize(
  "left,right", [(False, False), (False, True), (True, False), (True, True)]
)
def test_shared_boolean_operations_preserve_solver_context(
  left: bool, right: bool
) -> None:
  context = z3.Context()
  symbols = {
    "left": z3.Bool("left", ctx=context),
    "right": z3.Bool("right", ctx=context),
  }
  solver = z3.Solver(ctx=context)
  solver.add(symbols["left"] == left, symbols["right"] == right)
  solver.add(conjunction(disjunction(*symbols.values()), negation(symbols["right"])))
  assert (solver.check() == z3.sat) == ((left or right) and not right)


def test_shared_witness_completion_includes_unconstrained_symbols() -> None:
  context = z3.Context()
  symbols = {
    "known": z3.Bool("known", ctx=context),
    "unknown": z3.Bool("unknown", ctx=context),
  }
  solver = z3.Solver(ctx=context)
  solver.add(symbols["known"])
  assert solver.check() == z3.sat
  assert model_assignment(solver.model(), symbols) == {"known": True, "unknown": False}


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
    ambiguity = next(finding for finding in findings if finding.bug_type == "ambiguity")
    assert ambiguity.metadata["ambiguityRationale"] == "no threshold defined"
    assert ambiguity.metadata["interpretationARationale"] == "r"
    assert ambiguity.metadata["interpretationBRationale"] == "r"

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

  def test_partial_predicate_assignments_leave_other_predicates_free(self) -> None:
    model = SymbolicModel(
      predicates={"is_educational": "desc", "is_urgent": "desc"},
      safe_condition=((Literal("is_educational"),),),
      rules=(
        RuleSemantics(
          rule_id="rule:1",
          decision="block",
          condition=((Literal("is_educational"), Literal("is_urgent")),),
        ),
      ),
    )

    findings = SymbolicAnalyzer(
      model,
      _guardrail("rule:1"),
      predicate_assignments={"is_educational": True},
    ).analyze()

    assert [finding.bug_type for finding in findings] == ["over_strict"]
