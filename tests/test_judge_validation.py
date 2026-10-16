import json
from pathlib import Path

import pytest

from unmask.judge import AuditInput, ValidationError


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


def test_evidence_can_preserve_an_exact_whitespace_quote():
  replay = data()
  start = replay["rationale"].index(" ")
  replay["findings"][0]["evidence"] = [
    {"source": "rationale", "start": start, "end": start + 1, "quote": " "}
  ]

  parsed = AuditInput.parse(replay)

  assert parsed.findings[0].evidence[0].quote == " "


@pytest.mark.parametrize("field", ["id", "context", "response", "rationale"])
@pytest.mark.parametrize("surrogate", [0xD800, 0xDFFF])
def test_decoded_replay_rejects_lone_surrogates(field: str, surrogate: int) -> None:
  replay = data()
  replay[field] += chr(surrogate)
  with pytest.raises(ValidationError, match="invalid Unicode"):
    AuditInput.parse(replay)


@pytest.mark.parametrize("surrogate", [0xD800, 0xDFFF])
def test_json_replay_rejects_escaped_lone_surrogates(surrogate: int) -> None:
  replay = data()
  replay["context"] += chr(surrogate)
  with pytest.raises(ValidationError, match="invalid Unicode"):
    AuditInput.from_json(json.dumps(replay))


def test_json_replay_rejects_unescaped_lone_surrogates() -> None:
  with pytest.raises(ValidationError, match="invalid Unicode"):
    AuditInput.from_json('"' + chr(0xD800) + '"')


def test_json_replay_accepts_an_escaped_surrogate_pair() -> None:
  replay = data()
  replay["context"] += chr(0x1F6AB)
  parsed = AuditInput.from_json(json.dumps(replay, ensure_ascii=True))
  assert parsed.context == replay["context"]


def test_protocol_text_is_not_observed_procedural_evidence() -> None:
  replay = data()
  procedure_atoms = {
    atom["id"] for atom in replay["protocol"]["atoms"] if atom["role"] == "procedure"
  }
  finding = next(
    finding for finding in replay["findings"] if finding["atom"] in procedure_atoms
  )
  finding["evidence"] = [
    {
      "source": "protocol",
      "start": 0,
      "end": 1,
      "quote": replay["protocol"]["text"][:1],
    }
  ]
  with pytest.raises(ValidationError, match="invalid evidence source"):
    AuditInput.parse(replay)
