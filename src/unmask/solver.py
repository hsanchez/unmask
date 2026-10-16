"""Context-safe Boolean helpers, witnesses, and bounded entailment queries."""

import logging
from collections.abc import Mapping
from time import monotonic
from typing import NotRequired, TypedDict, cast

import z3

logger = logging.getLogger(__name__)


class QueryResult(TypedDict):
  """A proposition classification with optional solver evidence."""

  status: str
  true_query: NotRequired[str | None]
  false_query: NotRequired[str | None]
  witnesses: NotRequired[dict[str, dict[str, bool]]]
  reason: NotRequired[str | dict[str, str | None] | None]


def require_bool_ref(value: object) -> z3.BoolRef:
  """Narrow Z3's Boolean/probe overloads at the solver boundary.

  Raises:
    TypeError: if the expression is not a Z3 Boolean.
  """
  if not isinstance(value, z3.BoolRef):
    raise TypeError(f"expected a Z3 Boolean expression, got {type(value).__name__}")
  return value


def _validate_context(
  expressions: tuple[z3.BoolRef, ...], context: z3.Context | None
) -> None:
  if not expressions:
    return
  # Z3 can prefer operand contexts even when given a conflicting explicit one.
  selected_context = context if context is not None else expressions[0].ctx
  if any(expression.ctx != selected_context for expression in expressions):
    raise z3.Z3Exception(
      "context mismatch: Boolean operands must share the selected context"
    )


def conjunction(
  *expressions: z3.BoolRef, context: z3.Context | None = None
) -> z3.BoolRef:
  """Build an AND, including an empty (true) expression in the selected context.

  Without an explicit context, operands determine it; no operands use Z3's
  default context. Explicit contexts must match every operand's context.

  Raises:
    z3.Z3Exception: if operands and the selected context do not match.
  """
  _validate_context(expressions, context)
  if context is not None:
    return require_bool_ref(cast("object", z3.And(*expressions, context)))
  return require_bool_ref(cast("object", z3.And(*expressions)))


def disjunction(
  *expressions: z3.BoolRef, context: z3.Context | None = None
) -> z3.BoolRef:
  """Build an OR, including an empty (false) expression in the selected context.

  Without an explicit context, operands determine it; no operands use Z3's
  default context. Explicit contexts must match every operand's context.

  Raises:
    z3.Z3Exception: if operands and the selected context do not match.
  """
  _validate_context(expressions, context)
  if context is not None:
    return require_bool_ref(cast("object", z3.Or(*expressions, context)))
  return require_bool_ref(cast("object", z3.Or(*expressions)))


def negation(expression: z3.BoolRef) -> z3.BoolRef:
  return require_bool_ref(cast("object", z3.Not(expression)))


def model_assignment(
  model: z3.ModelRef, symbols: Mapping[str, z3.BoolRef]
) -> dict[str, bool]:
  return {
    name: z3.is_true(model.eval(symbol, model_completion=True))
    for name, symbol in symbols.items()
  }


class Engine:
  """Check base satisfiability before classifying each proposition."""

  def __init__(
    self,
    assertions: list[tuple[str, z3.BoolRef]],
    symbols: dict[str, z3.BoolRef],
    timeout_ms: int = 1000,
    deadline: float | None = None,
  ) -> None:
    self.symbols: dict[str, z3.BoolRef] = symbols
    self.timeout_ms: int = timeout_ms
    self.deadline: float | None = deadline
    self.context: z3.Context = (
      next(iter(symbols.values())).ctx if symbols else z3.main_ctx()
    )
    self.solver: z3.Solver = z3.Solver(ctx=self.context)
    self.check_reason: str | None = None
    self.labels: dict[str, str] = {}
    for index, (label, formula) in enumerate(assertions):
      # Tracking symbols cannot collide with validated user atom names.
      marker = z3.Bool(f"@track:{index}", ctx=self.context)
      self.labels[str(marker)] = label
      self.solver.assert_and_track(formula, marker)
    result = self._check()
    self.base: str = str(result)
    self.core: list[str] = []
    if result == z3.unsat:
      core = self.solver.unsat_core()
      for index in range(len(core)):
        core_marker = require_bool_ref(cast("object", core[index]))
        self.core.append(self.labels[str(core_marker)])
    self.reason: str | None = self.check_reason

  def _check(self, query: str = "base") -> z3.CheckSatResult:
    remaining = (
      self.timeout_ms
      if self.deadline is None
      else int((self.deadline - monotonic()) * 1000)
    )
    if remaining <= 0:
      self.check_reason = "audit time budget exhausted"
      result = z3.unknown
    else:
      self.solver.set(timeout=min(self.timeout_ms, remaining))
      result = self.solver.check()
      self.check_reason = self.solver.reason_unknown() if result == z3.unknown else None
    if result == z3.unknown:
      logger.warning(
        "solver %s query incomplete: %s",
        query,
        self.check_reason,
        extra={"solver_query": query, "timeout_ms": self.timeout_ms},
      )
    return result

  def query(self, proposition: z3.BoolRef) -> QueryResult:
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
    for label, expression in (("true", proposition), ("false", negation(proposition))):
      self.solver.push()
      try:
        self.solver.add(expression)
        result = self._check(label)
        results[label] = str(result)
        if result == z3.sat:
          models[label] = model_assignment(self.solver.model(), self.symbols)
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
