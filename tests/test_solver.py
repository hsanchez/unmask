"""Bounded entailment queries and the diagnostics they emit."""

import logging
from time import monotonic
from typing import cast

import pytest
import z3

from unmask.solver import Engine, require_bool_ref


def _symbol_table(*names: str) -> dict[str, z3.BoolRef]:
  return {name: z3.Bool(name) for name in names}


@pytest.mark.parametrize(
  "operator,values,expected",
  [
    ("or", [True, None], "ENTAILED"),
    ("and", [True, None], "UNDERDETERMINED"),
    ("and", [False, None], "REFUTED"),
    ("implies", [False, None], "ENTAILED"),
    ("iff", [True, True], "ENTAILED"),
    ("iff", [True, False], "REFUTED"),
  ],
)
def test_query_classifies_boolean_operators(
  operator: str, values: list[bool | None], expected: str
) -> None:
  symbols = _symbol_table("a", "b")
  first, second = symbols["a"], symbols["b"]
  proposition = require_bool_ref(
    cast(
      "object",
      {
        "or": z3.Or(first, second),
        "and": z3.And(first, second),
        "implies": z3.Implies(first, second),
        "iff": first == second,
      }[operator],
    )
  )
  assertions = [
    (name, require_bool_ref(cast("object", symbols[name] == value)))
    for name, value in zip(symbols, values, strict=True)
    if value is not None
  ]

  engine = Engine(assertions, symbols)

  assert engine.query(proposition)["status"] == expected


def test_unknown_base_result_proves_nothing() -> None:
  engine = Engine([], _symbol_table("a"))
  engine.base = "unknown"
  engine.reason = "injected timeout for deterministic branch test"

  result = engine.query(z3.Bool("a"))

  assert result["status"] == "SOLVER_UNKNOWN"
  assert result.get("reason") == "injected timeout for deterministic branch test"


def test_expired_budget_returns_unknown() -> None:
  engine = Engine([], _symbol_table("a"), deadline=monotonic() - 1)

  assert engine.query(z3.Bool("a"))["status"] == "SOLVER_UNKNOWN"
  assert engine.reason == "audit time budget exhausted"


def test_expired_budget_emits_query_diagnostics(
  caplog: pytest.LogCaptureFixture,
) -> None:
  _ = Engine([], _symbol_table("a"), deadline=monotonic() - 1)

  record = caplog.records[-1]
  assert record.__dict__["solver_query"] == "base"
  assert "audit time budget exhausted" in record.getMessage()


def test_unknown_queries_emit_separate_diagnostics_without_atom_names(
  caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
  symbols = _symbol_table("sensitive_predicate")
  symbol = symbols["sensitive_predicate"]
  engine = Engine([], symbols)
  monkeypatch.setattr(engine.solver, "check", lambda: z3.unknown)
  monkeypatch.setattr(engine.solver, "reason_unknown", lambda: "timeout")

  with caplog.at_level(logging.WARNING):
    result = engine.query(symbol)

  assert result["status"] == "SOLVER_UNKNOWN"
  assert result.get("reason") == {"true": "timeout", "false": "timeout"}
  assert [record.__dict__["solver_query"] for record in caplog.records] == [
    "true",
    "false",
  ]
  assert all(
    "sensitive_predicate" not in record.getMessage() for record in caplog.records
  )
