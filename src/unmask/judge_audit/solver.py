"""Boolean compilation and non-vacuous entailment queries."""

from time import monotonic
from typing import Any

import z3

from .models import Expr


def compile_expr(expression: Expr, symbols: dict[str, z3.BoolRef]) -> z3.BoolRef:
  if expression.op == "atom":
    return symbols[expression.atom or ""]
  if expression.op == "const":
    return z3.BoolVal(expression.value, ctx=next(iter(symbols.values())).ctx)
  arguments = [compile_expr(child, symbols) for child in expression.args]
  if expression.op == "and":
    return z3.And(*arguments)
  if expression.op == "or":
    return z3.Or(*arguments)
  if expression.op == "not":
    return z3.Not(arguments[0])
  if expression.op == "implies":
    return z3.Implies(*arguments)
  if expression.op == "iff":
    return arguments[0] == arguments[1]
  raise ValueError("unvalidated expression")


class Engine:
  """Check base satisfiability before classifying each proposition."""

  def __init__(
    self,
    assertions: list[tuple[str, z3.BoolRef]],
    symbols: dict[str, z3.BoolRef],
    timeout_ms: int = 1000,
    deadline: float | None = None,
  ) -> None:
    self.symbols = symbols
    self.timeout_ms = timeout_ms
    self.deadline = deadline
    self.context = next(iter(symbols.values())).ctx if symbols else z3.main_ctx()
    self.solver = z3.Solver(ctx=self.context)
    self.check_reason: str | None = None
    self.labels = {}
    for index, (label, formula) in enumerate(assertions):
      # Tracking symbols cannot collide with validated user atom names.
      marker = z3.Bool(f"@track:{index}", ctx=self.context)
      self.labels[str(marker)] = label
      self.solver.assert_and_track(formula, marker)
    result = self._check()
    self.base = str(result)
    self.core = (
      [self.labels[str(core_marker)] for core_marker in self.solver.unsat_core()]
      if result == z3.unsat
      else []
    )
    self.reason = self.check_reason

  def _check(self) -> z3.CheckSatResult:
    remaining = (
      self.timeout_ms
      if self.deadline is None
      else int((self.deadline - monotonic()) * 1000)
    )
    if remaining <= 0:
      self.check_reason = "audit time budget exhausted"
      return z3.unknown
    self.solver.set(timeout=min(self.timeout_ms, remaining))
    result = self.solver.check()
    self.check_reason = self.solver.reason_unknown() if result == z3.unknown else None
    return result

  def query(self, proposition: z3.BoolRef) -> dict[str, Any]:
    if self.base != "sat":
      return {
        "status": "INCONSISTENT_BASE" if self.base == "unsat" else "SOLVER_UNKNOWN",
        "true_query": None,
        "false_query": None,
        "witnesses": {},
        "reason": self.reason,
      }
    results: dict[str, str] = {}
    models: dict[str, dict[str, bool]] = {}
    reasons: dict[str, str | None] = {}
    for label, expression in (("true", proposition), ("false", z3.Not(proposition))):
      self.solver.push()
      try:
        self.solver.add(expression)
        result = self._check()
        results[label] = str(result)
        if result == z3.sat:
          model = self.solver.model()
          models[label] = {
            key: z3.is_true(model.eval(value, model_completion=True))
            for key, value in self.symbols.items()
          }
        elif result == z3.unknown:
          reasons[label] = self.check_reason
      finally:
        self.solver.pop()
    if "unknown" in results.values():
      status = "SOLVER_UNKNOWN"
    elif results["false"] == "unsat":
      status = "ENTAILED"
    elif results["true"] == "unsat":
      status = "REFUTED"
    else:
      status = "UNDERDETERMINED"
    return {
      "status": status,
      "true_query": results["true"],
      "false_query": results["false"],
      "witnesses": models,
      "reason": reasons or None,
    }
