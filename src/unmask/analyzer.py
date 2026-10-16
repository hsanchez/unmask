"""Z3-backed symbolic analysis of extracted guardrail models for provable defects."""

from __future__ import annotations

from itertools import combinations
from typing import TYPE_CHECKING, cast

from z3 import (
  Bool,
  BoolRef,
  Solver,
  Xor,
  sat,
)

from .models import Condition, Finding, Guardrail, SymbolicModel
from .solver import conjunction as _and
from .solver import disjunction as _or
from .solver import model_assignment, require_bool_ref
from .solver import negation as _not

if TYPE_CHECKING:
  from collections.abc import Mapping


def _bool(name: str) -> BoolRef:
  return require_bool_ref(cast("object", Bool(name)))


def _xor(left: BoolRef, right: BoolRef) -> BoolRef:
  return require_bool_ref(cast("object", Xor(left, right)))


class SymbolicAnalyzer:
  """Proves over-strict rules, contradictions, and ambiguities with Z3.

  Each proof is a satisfying predicate assignment (a witness) for a formula
  built from the extracted `SymbolicModel`, so findings are backed by a
  concrete counterexample rather than a heuristic.
  """

  def __init__(
    self,
    model: SymbolicModel,
    guardrail: Guardrail,
    predicate_assignments: Mapping[str, bool] | None = None,
  ) -> None:
    """
    Raises:
      ValueError: if the model's rule ids don't exactly match the guardrail's,
        an ambiguity references an unknown rule, or `predicate_assignments`
        references an unknown predicate.
    """
    expected_rule_ids: set[str] = {rule.id for rule in guardrail.rules}
    actual_rule_ids: set[str] = {rule.rule_id for rule in model.rules}
    if expected_rule_ids != actual_rule_ids:
      raise ValueError(
        f"Symbolic model rule IDs do not match the guardrail: missing={sorted(expected_rule_ids - actual_rule_ids)}, extra={sorted(actual_rule_ids - expected_rule_ids)}"
      )

    unknown_ambiguity_rules: set[str] = {
      ambiguity.rule_id for ambiguity in model.ambiguities
    } - expected_rule_ids
    if unknown_ambiguity_rules:
      raise ValueError(
        f"Ambiguities reference unknown guardrail rules: {sorted(unknown_ambiguity_rules)}"
      )

    self._model: SymbolicModel = model
    self._variables: dict[str, BoolRef] = {
      name: _bool(name) for name in model.predicates
    }
    self._global_constraints: list[BoolRef] = self._build_global_constraints()

    if predicate_assignments is not None:
      unknown_assignments: set[str] = set(predicate_assignments) - set(self._variables)
      if unknown_assignments:
        raise ValueError(
          f"predicate assignments reference unknown predicates: {sorted(unknown_assignments)}"
        )

      for name, variable in self._variables.items():
        if name not in predicate_assignments:
          continue
        assignment: BoolRef = (
          variable if predicate_assignments[name] else _not(variable)
        )
        self._global_constraints.append(assignment)

  def analyze(self) -> list[Finding]:
    """Run all three defect checks and return findings sorted by type and id."""
    findings: list[Finding] = []
    findings.extend(self._find_over_strict_rules())
    findings.extend(self._find_contradictions())
    findings.extend(self._find_ambiguities())
    return sorted(findings, key=lambda finding: (finding.bug_type, finding.id))

  def _condition(self, condition: Condition) -> BoolRef:
    clauses: list[BoolRef] = [
      _and(
        *[
          self._variables[literal.predicate]
          if literal.value
          else _not(self._variables[literal.predicate])
          for literal in clause
        ]
      )
      for clause in condition
    ]
    return _or(*clauses)

  def _build_global_constraints(self) -> list[BoolRef]:
    constraints: list[BoolRef] = []
    for exclusion in self._model.exclusions:
      for left, right in combinations(exclusion, 2):
        constraints.append(_not(_and(self._variables[left], self._variables[right])))
    return constraints

  def _witness(self, *conditions: BoolRef) -> dict[str, bool] | None:
    solver = Solver()
    solver.add(*self._global_constraints, *conditions)
    if solver.check() != sat:
      return None
    return model_assignment(solver.model(), self._variables)

  def _find_over_strict_rules(self) -> list[Finding]:
    safe: BoolRef = self._condition(self._model.safe_condition)
    findings: list[Finding] = []
    for rule in self._model.rules:
      if rule.decision == "allow":
        continue
      witness: dict[str, bool] | None = self._witness(
        safe, self._condition(rule.condition)
      )
      if witness is None:
        continue
      findings.append(
        Finding(
          id=f"over-strict:{rule.rule_id}",
          bug_type="over_strict",
          severity="high",
          rule_ids=(rule.rule_id,),
          summary=f"Rule {rule.rule_id} can block a semantically safe request",
          rationale=(
            "Z3 found an assignment satisfying both the independently extracted "
            "safe condition and this rule's blocking condition."
          ),
          witness=witness,
        )
      )
    return findings

  def _find_contradictions(self) -> list[Finding]:
    findings: list[Finding] = []
    for left, right in combinations(self._model.rules, 2):
      if left.decision == right.decision:
        continue
      witness: dict[str, bool] | None = self._witness(
        _and(self._condition(left.condition), self._condition(right.condition))
      )
      if witness is None:
        continue
      ordered: tuple[str, ...] = tuple(sorted([left.rule_id, right.rule_id]))
      findings.append(
        Finding(
          id=f"contradiction:{ordered[0]}:{ordered[1]}",
          bug_type="contradiction",
          severity="high",
          rule_ids=ordered,
          summary=f"{ordered[0]} and {ordered[1]} can require opposite decisions",
          rationale=(
            "Z3 found an input class where an allow rule and a block rule both "
            "apply. No precedence is represented by the directions."
          ),
          witness=witness,
          metadata={
            left.rule_id: left.decision,
            right.rule_id: right.decision,
          },
        )
      )
    return findings

  def _find_ambiguities(self) -> list[Finding]:
    findings: list[Finding] = []
    for index, item in enumerate(self._model.ambiguities, start=1):
      left: BoolRef = self._condition(item.interpretation_a.condition)
      right: BoolRef = self._condition(item.interpretation_b.condition)
      divergence: BoolRef = (
        _xor(left, right)
        if item.interpretation_a.decision == item.interpretation_b.decision
        else _or(left, right)
      )
      witness: dict[str, bool] | None = self._witness(divergence)
      if witness is None:
        continue
      findings.append(
        Finding(
          id=f"ambiguity:{item.rule_id}:{index}",
          bug_type="ambiguity",
          severity="medium",
          rule_ids=(item.rule_id,),
          summary=f"Rule {item.rule_id} contains '{item.category}' ambiguity",
          rationale=(
            "Z3 found an assignment where the two plausible "
            "interpretations differ in applicability or decision."
          ),
          witness=witness,
          metadata={
            "category": item.category,
            "excerpt": item.excerpt,
            "ambiguityRationale": item.rationale,
            "interpretationA": item.interpretation_a.label,
            "interpretationADecision": item.interpretation_a.decision,
            "interpretationARationale": item.interpretation_a.rationale,
            "interpretationB": item.interpretation_b.label,
            "interpretationBDecision": item.interpretation_b.decision,
            "interpretationBRationale": item.interpretation_b.rationale,
          },
        )
      )
    return findings
