import json
from pathlib import Path

import pytest

from unmask.judge_audit.models import AuditInput, ValidationError


def data():
  return json.loads(
    next(
      (Path(__file__).resolve().parent / "fixtures/judge_audit").glob("A_*.json")
    ).read_text()
  )


@pytest.mark.parametrize(
  "mutate",
  [
    lambda d: d.update(schema_version=True),
    lambda d: d.update(verdict="MAYBE"),
    lambda d: d.update(unexpected="field"),
    lambda d: d["protocol"]["atoms"].append(d["protocol"]["atoms"][0]),
    lambda d: d["protocol"].update(decision={"atom": "undeclared"}),
    lambda d: d["protocol"].update(decision={"atom": "c1"}),
    lambda d: d["protocol"].update(decision={"op": "exec", "args": []}),
    lambda d: d["protocol"].update(decision={"op": "and", "args": []}),
    lambda d: d["protocol"].update(decision={"const": 1}),
    lambda d: d["protocol"].update(
      decision="__import__('os').system('touch /tmp/evil')"
    ),
    lambda d: d["findings"][0].update(evidence=[]),
    lambda d: d["findings"][0].update(value=True),
    lambda d: d["findings"][0]["evidence"][0].update(quote="made up"),
    lambda d: d["findings"][0]["evidence"][0].update(start=-1),
    lambda d: d["findings"][0]["evidence"][0].update(start=True),
    lambda d: d["findings"][0]["evidence"][0].update(source="context"),
    lambda d: d.update(grounding_pairs=[["c1", "j1"]]),
    lambda d: d["protocol"]["requirements"].append(d["protocol"]["requirements"][0]),
  ],
)
def test_invalid_ir_is_rejected(mutate):
  d = data()
  mutate(d)
  with pytest.raises(ValidationError):
    AuditInput.parse(d)


def test_duplicate_json_key():
  with pytest.raises(ValidationError, match="duplicate"):
    AuditInput.from_json('{"id":"a","id":"b"}')


def test_oversize_bytes():
  with pytest.raises(ValidationError, match="byte limit"):
    AuditInput.from_json(" " * 1_000_001)


def test_expression_depth():
  d = data()
  expr = {"atom": "j1"}
  for _ in range(18):
    expr = {"op": "not", "args": [expr]}
  d["protocol"]["decision"] = expr
  with pytest.raises(ValidationError, match="resource limit"):
    AuditInput.parse(d)


def test_atom_limit():
  d = data()
  d["protocol"]["atoms"] *= 17
  with pytest.raises(ValidationError, match="128"):
    AuditInput.parse(d)


def test_unknown_needs_no_fabricated_evidence():
  d = data()
  d["findings"][0].update(value="UNKNOWN", evidence=[])
  AuditInput.parse(d)
