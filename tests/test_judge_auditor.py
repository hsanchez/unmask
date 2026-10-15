import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import z3

from unmask.judge_audit.auditor import audit
from unmask.judge_audit.models import AuditInput, Expr
from unmask.judge_audit.solver import Engine, compile_expr

EXAMPLES = Path(__file__).resolve().parent / "fixtures/judge_audit"


def load(prefix="A_"):
  return json.loads(next(EXAMPLES.glob(prefix + "*.json")).read_text())


def run(data):
  return audit(AuditInput.parse(data))


@pytest.mark.parametrize(
  "prefix,correct,procedure,overall,consistency",
  [
    ("A_", True, "SATISFIED", "SATISFIED", True),
    ("B_", True, "VIOLATED", "VIOLATED", True),
    ("C_", False, "SATISFIED", "VIOLATED", True),
    ("D_", False, "VIOLATED", "VIOLATED", False),
  ],
)
def test_four_cells(prefix, correct, procedure, overall, consistency):
  report = run(load(prefix))
  assert report["verdict_correct"] is correct
  assert report["procedural_adherence"] == procedure
  assert report["protocol_adherent"] == overall
  assert report["rationale_verdict_consistent"] is consistency


def test_unknown_remains_unconstrained():
  report = run(load("unknown"))
  assert report["rationale_verdict"]["status"] == "UNDERDETERMINED"
  assert report["rationale_verdict"]["true_query"] == "sat"
  assert report["rationale_verdict"]["false_query"] == "sat"
  assert report["verdict_entailed"] is False
  assert report["expected_verdict_under_stated_findings"] is None
  assert report["rationale_verdict"]["witnesses"]["true"]["j2"] is True
  assert report["rationale_verdict"]["witnesses"]["false"]["j2"] is False


def test_inconsistent_protocol_not_vacuously_entailed():
  report = run(load("contradictory"))
  assert report["status"] == "INCONSISTENT_PROTOCOL"
  assert report["verdict_entailed"] is None
  assert report["protocol_adherent"] == "UNKNOWN"
  assert report["solver"]["unsat_core"]


def test_conflicting_findings_not_proof():
  data = load()
  bad = copy.deepcopy(data["findings"][0])
  bad["value"] = "FALSE"
  data["findings"].append(bad)
  report = run(data)
  assert report["status"] == "INCONSISTENT_FINDINGS"
  assert report["verdict_entailed"] is None


def test_protocol_fact_conflict():
  data = load()
  data["protocol"]["constraints"] = [
    {
      "id": "conflict",
      "description": "Forbid j1",
      "expr": {"op": "not", "args": [{"atom": "j1"}]},
    }
  ]
  assert run(data)["status"] == "INCONSISTENT_BASE"


def test_gold_is_not_solver_input():
  data = load()
  before = run(data)
  data["gold_verdict"] = "FAIL"
  after = run(data)
  assert before["verdict_correct"] != after["verdict_correct"]
  for report in (before, after):
    report.pop("gold_verdict")
    report.pop("verdict_correct")
  assert before == after


def test_external_violation_does_not_rewrite_decision():
  data = load()
  next(f for f in data["findings"] if f["atom"] == "external")["value"] = "TRUE"
  report = run(data)
  assert report["procedural_adherence"] == "VIOLATED"
  assert report["rationale_verdict"]["status"] == "ENTAILED"


def test_missing_fact_is_unknown_not_false():
  data = load()
  data["findings"] = [f for f in data["findings"] if f["atom"] != "external"]
  assert run(data)["procedural_adherence"] == "UNKNOWN"


def test_translation_invalid_and_unknown():
  data = load("invalid")
  assert run(data)["status"] == "TRANSLATION_INVALID"
  data["extraction"]["translation_status"] = "UNKNOWN"
  assert run(data)["status"] == "TRANSLATION_UNKNOWN"


@pytest.mark.parametrize(
  "op,values,expected",
  [
    ("or", [True, None], "ENTAILED"),
    ("and", [True, None], "UNDERDETERMINED"),
    ("and", [False, None], "REFUTED"),
    ("implies", [False, None], "ENTAILED"),
    ("iff", [True, True], "ENTAILED"),
    ("iff", [True, False], "REFUTED"),
  ],
)
def test_boolean_formula_semantics(op, values, expected):
  symbols = {"a": z3.Bool("a"), "b": z3.Bool("b")}
  facts = [
    (key, symbols[key] == value)
    for key, value in zip(symbols, values, strict=True)
    if value is not None
  ]
  expr = Expr.parse(
    {"op": op, "args": [{"atom": "a"}, {"atom": "b"}]}, set(symbols), [0]
  )
  assert Engine(facts, symbols).query(compile_expr(expr, symbols))["status"] == expected


def test_solver_unknown_does_not_prove_anything():
  engine = Engine([], {"a": z3.Bool("a")})
  engine.base = "unknown"
  engine.reason = "injected timeout for deterministic branch test"
  result = engine.query(z3.Bool("a"))
  assert result["status"] == "SOLVER_UNKNOWN"
  assert "timeout" in result["reason"]


def test_cli_invalid_json(tmp_path):
  path = tmp_path / "bad.json"
  path.write_text('{"bad":')
  result = subprocess.run(
    [sys.executable, "-m", "unmask.cli", "audit-judge", str(path)],
    capture_output=True,
    text=True,
  )
  assert result.returncode == 1
  assert "invalid JSON" in result.stderr


def test_omitted_dimensions_prevent_positive_overall():
  data = load()
  data["protocol"]["requirements"] = []
  data["grounding_pairs"] = []
  report = run(data)
  assert report["procedural_adherence"] == "UNKNOWN"
  assert report["grounding_correctness"] == "UNKNOWN"
  assert report["protocol_adherent"] == "UNKNOWN"
