import itertools
import random

import pytest

from unmask.judge import ValidationError, VerificationCase, audit


def data(decision=None, findings=None):
  return {
    "schema_version": 1,
    "id": "review",
    "context": "x",
    "response": "x",
    "rationale": "x",
    "verdict": "PASS",
    "protocol": {
      "text": "x",
      "atoms": [{"id": s, "role": "judge", "description": s} for s in ["A", "B"]],
      "decision": decision or {"atom": "A"},
      "constraints": [],
      "requirements": [],
    },
    "findings": findings or [],
    "extraction": {"provider": "review", "translation_status": "VALID"},
  }


def finding(atom, value):
  return {
    "atom": atom,
    "value": value,
    "evidence": []
    if value == "UNKNOWN"
    else [{"source": "rationale", "start": 0, "end": 1, "quote": "x"}],
    "explanation": "review",
  }


def expr(op, *args):
  return {"op": op, "args": list(args)}


a = {"atom": "A"}
b = {"atom": "B"}


@pytest.mark.parametrize(
  "op,vals,expected",
  [
    ("or", ("TRUE", "UNKNOWN"), "ENTAILED"),
    ("and", ("TRUE", "UNKNOWN"), "UNDERDETERMINED"),
    ("implies", ("TRUE", "UNKNOWN"), "UNDERDETERMINED"),
    ("implies", ("FALSE", "UNKNOWN"), "ENTAILED"),
    ("and", ("FALSE", "UNKNOWN"), "REFUTED"),
    ("or", ("FALSE", "UNKNOWN"), "UNDERDETERMINED"),
    ("iff", ("TRUE", "UNKNOWN"), "UNDERDETERMINED"),
  ],
)
def test_unknown_truth_table(op, vals, expected):
  d = data(expr(op, a, b), [finding("A", vals[0]), finding("B", vals[1])])
  assert audit(VerificationCase.parse(d))["rationale_verdict"]["status"] == expected


def test_inconsistency():
  d = data(findings=[finding("A", "TRUE"), finding("A", "FALSE")])
  r = audit(VerificationCase.parse(d))
  assert r["status"] == "INCONSISTENT_FINDINGS"
  assert r["rationale_verdict"]["status"] == "INCONSISTENT_BASE"
  assert r["verdict_entailed"] is None


def test_excluded_middle():
  for decision, expected in [
    (expr("or", a, expr("not", a)), "ENTAILED"),
    (expr("and", a, expr("not", a)), "REFUTED"),
  ]:
    assert (
      audit(VerificationCase.parse(data(decision)))["rationale_verdict"]["status"]
      == expected
    )


def test_no_grounding_contamination():
  d = data(findings=[finding("A", "TRUE")])
  d["protocol"]["atoms"].append(
    {"id": "C", "role": "context", "description": "independent"}
  )
  f = finding("C", "FALSE")
  f["evidence"][0]["source"] = "context"
  d["findings"].append(f)
  d["grounding_pairs"] = [["A", "C"]]
  r = audit(VerificationCase.parse(d))
  assert r["rationale_verdict"]["status"] == "ENTAILED"
  assert r["grounding_correctness"] == "VIOLATED"
  assert r["status"] == "OK"


def test_requirement_not_folded():
  d = data(findings=[finding("A", "TRUE"), finding("B", "FALSE")])
  d["protocol"]["requirements"] = [
    {"id": "req", "description": "x", "expr": b, "addressed": {"const": True}}
  ]
  r = audit(VerificationCase.parse(d))
  assert r["rationale_verdict"]["status"] == "ENTAILED"
  assert r["procedural_adherence"] == "VIOLATED"


def test_witness_pass_collision():
  d = data(findings=[finding("A", "TRUE")])
  d["protocol"]["atoms"].append(
    {"id": "PASS", "role": "judge", "description": "user atom PASS"}
  )
  d["findings"].append(finding("PASS", "FALSE"))
  r = audit(VerificationCase.parse(d))
  # User atom PASS is false, even though verdict is PASS. Must not lose it.
  assert r["rationale_verdict"]["witnesses"]["true"]["PASS"] is False


@pytest.mark.parametrize(
  "bad",
  [
    {},
    [],
    None,
    1,
    True,
    "A",
    {"const": 1},
    {"op": "eval", "args": []},
    {"atom": "__import__"},
    {"op": "and", "args": []},
  ],
)
def test_malformed_expr(bad):
  d = data()
  d["protocol"]["decision"] = bad
  with pytest.raises(ValidationError):
    VerificationCase.parse(d)


def test_random_formulas_against_exhaustive_truth_tables():
  rng = random.Random(491)

  def gen(n):
    if n == 0 or rng.random() < 0.3:
      return {"atom": rng.choice(["A", "B"])}
    op = rng.choice(["and", "or", "not", "implies", "iff"])
    return expr(op, *(gen(n - 1) for _ in range(1 if op == "not" else 2)))

  def ev(e, m):
    if "atom" in e:
      return m[e["atom"]]
    if "const" in e:
      return e["const"]
    x = [ev(a, m) for a in e["args"]]
    op = e["op"]
    return {
      "and": lambda: all(x),
      "or": lambda: any(x),
      "not": lambda: not x[0],
      "implies": lambda: not x[0] or x[1],
      "iff": lambda: x[0] == x[1],
    }[op]()

  for _ in range(150):
    dec = gen(3)
    constraint = gen(2)
    fs = {s: rng.choice(["TRUE", "FALSE", "UNKNOWN"]) for s in ["A", "B"]}
    outcomes = []
    for av, bv in itertools.product([False, True], repeat=2):
      m = {"A": av, "B": bv}
      if ev(constraint, m) and all(
        v == "UNKNOWN" or m[s] == (v == "TRUE") for s, v in fs.items()
      ):
        outcomes.append(ev(dec, m))
    d = data(dec, [finding(s, v) for s, v in fs.items()])
    d["protocol"]["constraints"] = [{"id": "c", "description": "x", "expr": constraint}]
    observed = rng.choice(["PASS", "FAIL"])
    d["verdict"] = observed
    r = audit(VerificationCase.parse(d))
    expected = (
      "INCONSISTENT_BASE"
      if not outcomes
      else "ENTAILED"
      if all(v == (observed == "PASS") for v in outcomes)
      else "REFUTED"
      if all(v != (observed == "PASS") for v in outcomes)
      else "UNDERDETERMINED"
    )
    assert r["rationale_verdict"]["status"] == expected
