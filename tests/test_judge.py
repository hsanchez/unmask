"""Replay validation, expression compilation, audit semantics, and wire reports."""

import hashlib
import itertools
import json
import random
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import cast

import pytest
import z3

from tests.helpers import (
  json_integer,
  json_list,
  json_object,
  json_text,
  load_json_object,
  present,
  replay_object,
  replay_text,
)
from unmask.judge import (
  AuditReport,
  Expr,
  ValidationError,
  VerificationCase,
  audit,
  audit_judge_case,
  compile_expr,
  validate_audit_case,
)

Mutation = Callable[[dict[str, object]], object]


def _audit_data(data: dict[str, object]) -> AuditReport:
  return audit(VerificationCase.parse(data))


def _protocol(data: dict[str, object]) -> dict[str, object]:
  return json_object(data["protocol"])


def _findings(data: dict[str, object]) -> list[object]:
  return json_list(data["findings"])


def _finding_for(data: dict[str, object], atom: str) -> dict[str, object]:
  for finding in _findings(data):
    candidate = json_object(finding)
    if candidate["atom"] == atom:
      return candidate
  raise LookupError(atom)


def _first_evidence(data: dict[str, object]) -> dict[str, object]:
  first_finding = json_object(_findings(data)[0])
  return json_object(json_list(first_finding["evidence"])[0])


def _verdict_status(report: AuditReport) -> str:
  return present(report["rationale_verdict"])["status"]


def _expression(operator: str, *arguments: dict[str, object]) -> dict[str, object]:
  return {"op": operator, "args": list(arguments)}


ATOM_A: dict[str, object] = {"atom": "A"}
ATOM_B: dict[str, object] = {"atom": "B"}


def _minimal_case(
  decision: dict[str, object] | None = None,
  findings: list[dict[str, object]] | None = None,
) -> dict[str, object]:
  return {
    "schema_version": 1,
    "id": "minimal",
    "context": "x",
    "response": "x",
    "rationale": "x",
    "verdict": "PASS",
    "protocol": {
      "text": "x",
      "atoms": [
        {"id": name, "role": "judge", "description": name} for name in ("A", "B")
      ],
      "decision": decision or ATOM_A,
      "constraints": [],
      "requirements": [],
    },
    "findings": findings or [],
    "extraction": {"provider": "test", "translation_status": "VALID"},
  }


def _observation(atom: str, value: str) -> dict[str, object]:
  evidence: list[dict[str, object]] = (
    []
    if value == "UNKNOWN"
    else [{"source": "rationale", "start": 0, "end": 1, "quote": "x"}]
  )
  return {"atom": atom, "value": value, "evidence": evidence, "explanation": "test"}


# --- Expression compilation -------------------------------------------------


@pytest.mark.parametrize("operator,identity", [("and", True), ("or", False)])
def test_compiled_boolean_operations_keep_the_audit_context(
  operator: str, identity: bool
) -> None:
  context = z3.Context()
  symbol = z3.Bool("a", ctx=context)
  expression = Expr.parse(
    {"op": operator, "args": [{"const": identity}, {"atom": "a"}]}, {"a"}, [0]
  )

  compiled = compile_expr(expression, {"a": symbol})

  solver = z3.Solver(ctx=context)
  solver.add(compiled, z3.Not(symbol))
  assert compiled.ctx == context
  assert solver.check() == z3.unsat


@pytest.mark.parametrize(
  "raw_expression,expected",
  [
    ({"const": True}, True),
    ({"const": False}, False),
    ({"op": "and", "args": [{"const": True}, {"const": False}]}, False),
    ({"op": "or", "args": [{"const": False}, {"const": True}]}, True),
    ({"op": "not", "args": [{"const": False}]}, True),
    ({"op": "implies", "args": [{"const": True}, {"const": False}]}, False),
    ({"op": "iff", "args": [{"const": False}, {"const": False}]}, True),
  ],
)
def test_ground_formulas_compile_without_dummy_symbols(
  raw_expression: dict[str, object], expected: bool
) -> None:
  context = z3.Context()
  expression = Expr.parse(raw_expression, set(), [0])

  compiled = compile_expr(expression, {}, context=context)

  solver = z3.Solver(ctx=context)
  solver.add(compiled)
  assert compiled.ctx == context
  assert solver.check() == (z3.sat if expected else z3.unsat)


@pytest.mark.parametrize("value", [False, True])
def test_ground_constants_default_to_the_main_context(value: bool) -> None:
  compiled = compile_expr(Expr("const", value=value), {})

  assert compiled.ctx == z3.main_ctx()
  assert z3.is_true(compiled) is value


def test_compilation_rejects_an_incompatible_explicit_context() -> None:
  operand_context = z3.Context()
  selected_context = z3.Context()

  with pytest.raises(z3.Z3Exception, match="context mismatch"):
    _ = compile_expr(
      Expr("atom", atom="a"),
      {"a": z3.Bool("a", ctx=operand_context)},
      context=selected_context,
    )


# --- Replay validation ------------------------------------------------------


def _set(key: str, value: object) -> Mutation:
  return lambda data: data.update({key: value})


def _set_evidence(key: str, value: object) -> Mutation:
  return lambda data: _first_evidence(data).update({key: value})


def _duplicate_first_atom(data: dict[str, object]) -> None:
  atoms = json_list(_protocol(data)["atoms"])
  atoms.append(atoms[0])


def _duplicate_first_requirement(data: dict[str, object]) -> None:
  requirements = json_list(_protocol(data)["requirements"])
  requirements.append(requirements[0])


def _drop_evidence(data: dict[str, object]) -> None:
  json_object(_findings(data)[0])["evidence"] = []


def _non_text_value(data: dict[str, object]) -> None:
  json_object(_findings(data)[0])["value"] = True


@pytest.mark.parametrize(
  "mutate",
  [
    pytest.param(_set("schema_version", True), id="boolean-schema-version"),
    pytest.param(_set("verdict", "MAYBE"), id="unknown-verdict"),
    pytest.param(_set("unexpected", "field"), id="unexpected-field"),
    pytest.param(_duplicate_first_atom, id="duplicate-atom"),
    pytest.param(_drop_evidence, id="missing-evidence"),
    pytest.param(_non_text_value, id="boolean-observation"),
    pytest.param(_set_evidence("quote", "made up"), id="quote-not-in-source"),
    pytest.param(_set_evidence("start", -1), id="negative-span"),
    pytest.param(_set_evidence("start", True), id="boolean-span"),
    pytest.param(_set_evidence("source", "context"), id="wrong-evidence-source"),
    pytest.param(_set("grounding_pairs", [["c1", "j1"]]), id="reversed-grounding"),
    pytest.param(_duplicate_first_requirement, id="duplicate-requirement"),
    pytest.param(_set("protocol", []), id="protocol-not-an-object"),
    pytest.param(_set("findings", {}), id="findings-not-a-list"),
    pytest.param(_set_evidence("quote", 5), id="non-text-quote"),
  ],
)
def test_invalid_replay_is_rejected(mutate: Mutation) -> None:
  data = replay_object()
  _ = mutate(data)

  with pytest.raises(ValidationError):
    _ = VerificationCase.parse(data)


@pytest.mark.parametrize(
  "decision",
  [
    {"atom": "undeclared"},
    {"atom": "c1"},
    {"atom": "__import__"},
    {"op": "exec", "args": []},
    {"op": "eval", "args": []},
    {"op": 3, "args": []},
    {"op": "and", "args": []},
    {"const": 1},
    "__import__('os').system('touch /tmp/evil')",
    "A",
    {},
    [],
    None,
    1,
    True,
  ],
)
def test_invalid_decision_expression_is_rejected(decision: object) -> None:
  data = replay_object()
  _protocol(data)["decision"] = decision

  with pytest.raises(ValidationError):
    _ = VerificationCase.parse(data)


@pytest.mark.parametrize(
  "text,message",
  [
    ('{"id":"a","id":"b"}', "duplicate"),
    (" " * 1_000_001, "byte limit"),
    ('{"id":NaN}', "invalid JSON constant"),
  ],
  ids=["duplicate-key", "oversize", "non-finite-number"],
)
def test_json_boundary_rejects_invalid_text(text: str, message: str) -> None:
  with pytest.raises(ValidationError, match=message):
    _ = VerificationCase.from_json(text)


def test_expression_depth_is_bounded() -> None:
  data = replay_object()
  expression: dict[str, object] = {"atom": "j1"}
  for _ in range(18):
    expression = _expression("not", expression)
  _protocol(data)["decision"] = expression

  with pytest.raises(ValidationError, match="resource limit"):
    _ = VerificationCase.parse(data)


def test_atom_count_is_bounded() -> None:
  data = replay_object()
  _protocol(data)["atoms"] = json_list(_protocol(data)["atoms"]) * 17

  with pytest.raises(ValidationError, match="128"):
    _ = VerificationCase.parse(data)


def test_unknown_observation_needs_no_fabricated_evidence() -> None:
  data = replay_object()
  json_object(_findings(data)[0]).update(value="UNKNOWN", evidence=[])

  _ = VerificationCase.parse(data)


def test_evidence_can_preserve_an_exact_whitespace_quote() -> None:
  data = replay_object()
  start = json_text(data["rationale"]).index(" ")
  json_object(_findings(data)[0])["evidence"] = [
    {"source": "rationale", "start": start, "end": start + 1, "quote": " "}
  ]

  parsed = VerificationCase.parse(data)

  assert parsed.annotations[0].evidence[0].quote == " "


@pytest.mark.parametrize("field", ["id", "context", "response", "rationale"])
@pytest.mark.parametrize("surrogate", [0xD800, 0xDFFF])
def test_decoded_replay_rejects_lone_surrogates(field: str, surrogate: int) -> None:
  data = replay_object()
  data[field] = json_text(data[field]) + chr(surrogate)

  with pytest.raises(ValidationError, match="invalid Unicode"):
    _ = VerificationCase.parse(data)


@pytest.mark.parametrize("surrogate", [0xD800, 0xDFFF])
def test_json_replay_rejects_escaped_lone_surrogates(surrogate: int) -> None:
  data = replay_object()
  data["context"] = json_text(data["context"]) + chr(surrogate)

  with pytest.raises(ValidationError, match="invalid Unicode"):
    _ = VerificationCase.from_json(json.dumps(data))


def test_json_replay_rejects_unescaped_lone_surrogates() -> None:
  with pytest.raises(ValidationError, match="invalid Unicode"):
    _ = VerificationCase.from_json('"' + chr(0xD800) + '"')


def test_json_replay_accepts_an_escaped_surrogate_pair() -> None:
  data = replay_object()
  data["context"] = json_text(data["context"]) + chr(0x1F6AB)

  parsed = VerificationCase.from_json(json.dumps(data, ensure_ascii=True))

  assert parsed.context == data["context"]


def test_protocol_text_is_not_observed_procedural_evidence() -> None:
  data = replay_object()
  procedure_atoms = {
    json_object(atom)["id"]
    for atom in json_list(_protocol(data)["atoms"])
    if json_object(atom)["role"] == "procedure"
  }
  finding = next(
    json_object(item)
    for item in _findings(data)
    if json_object(item)["atom"] in procedure_atoms
  )
  protocol_text = json_text(_protocol(data)["text"])
  finding["evidence"] = [
    {"source": "protocol", "start": 0, "end": 1, "quote": protocol_text[:1]}
  ]

  with pytest.raises(ValidationError, match="invalid evidence source"):
    _ = VerificationCase.parse(data)


# --- Audit semantics --------------------------------------------------------


@pytest.mark.parametrize(
  "prefix,correct,procedure,overall,consistent",
  [
    ("A_", True, "SATISFIED", "SATISFIED", True),
    ("B_", True, "VIOLATED", "VIOLATED", True),
    ("C_", False, "SATISFIED", "VIOLATED", True),
    ("D_", False, "VIOLATED", "VIOLATED", False),
  ],
)
def test_verdict_correctness_and_procedure_vary_independently(
  prefix: str, correct: bool, procedure: str, overall: str, consistent: bool
) -> None:
  report = _audit_data(replay_object(prefix))

  assert report["verdict_correct"] is correct
  assert report["procedural_adherence"] == procedure
  assert report["protocol_adherent"] == overall
  assert report.get("rationale_verdict_consistent") is consistent


@pytest.mark.parametrize(
  "operator,values,expected",
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
def test_unknown_observations_stay_unconstrained(
  operator: str, values: tuple[str, str], expected: str
) -> None:
  data = _minimal_case(
    _expression(operator, ATOM_A, ATOM_B),
    [_observation("A", values[0]), _observation("B", values[1])],
  )

  assert _verdict_status(_audit_data(data)) == expected


def test_unknown_support_reports_witnesses_for_both_outcomes() -> None:
  report = _audit_data(replay_object("unknown"))
  verdict = present(report["rationale_verdict"])

  assert verdict["status"] == "UNDERDETERMINED"
  assert verdict.get("true_query") == "sat"
  assert verdict.get("false_query") == "sat"
  assert report.get("verdict_entailed") is False
  assert report.get("expected_verdict_under_stated_findings") is None
  witnesses = verdict.get("witnesses", {})
  assert witnesses["true"]["j2"] is True
  assert witnesses["false"]["j2"] is False


def test_conflicting_observations_prove_nothing() -> None:
  data = _minimal_case(findings=[_observation("A", "TRUE"), _observation("A", "FALSE")])

  report = _audit_data(data)

  assert report["status"] == "INCONSISTENT_FINDINGS"
  assert _verdict_status(report) == "INCONSISTENT_BASE"
  assert report.get("verdict_entailed") is None


def test_inconsistent_protocol_is_not_vacuously_entailed() -> None:
  report = _audit_data(replay_object("contradictory"))

  assert report["status"] == "INCONSISTENT_PROTOCOL"
  assert report.get("verdict_entailed") is None
  assert report["protocol_adherent"] == "UNKNOWN"
  assert present(report.get("solver"))["unsat_core"]


def test_protocol_constraint_conflicting_with_observed_fact_is_inconsistent() -> None:
  data = replay_object()
  _protocol(data)["constraints"] = [
    {
      "id": "conflict",
      "description": "Forbid j1",
      "expr": _expression("not", {"atom": "j1"}),
    }
  ]

  assert _audit_data(data)["status"] == "INCONSISTENT_BASE"


@pytest.mark.parametrize(
  "decision,expected",
  [
    (_expression("or", ATOM_A, _expression("not", ATOM_A)), "ENTAILED"),
    (_expression("and", ATOM_A, _expression("not", ATOM_A)), "REFUTED"),
  ],
  ids=["tautology", "contradiction"],
)
def test_decision_validity_does_not_depend_on_observations(
  decision: dict[str, object], expected: str
) -> None:
  assert _verdict_status(_audit_data(_minimal_case(decision))) == expected


def test_grounding_mismatch_does_not_contaminate_the_verdict() -> None:
  data = _minimal_case(findings=[_observation("A", "TRUE")])
  json_list(_protocol(data)["atoms"]).append(
    {"id": "C", "role": "context", "description": "independent"}
  )
  context_observation = _observation("C", "FALSE")
  json_object(json_list(context_observation["evidence"])[0])["source"] = "context"
  _findings(data).append(context_observation)
  data["grounding_pairs"] = [["A", "C"]]

  report = _audit_data(data)

  assert _verdict_status(report) == "ENTAILED"
  assert report.get("grounding_correctness") == "VIOLATED"
  assert report["status"] == "OK"


def test_requirement_violation_is_not_folded_into_the_verdict() -> None:
  data = _minimal_case(findings=[_observation("A", "TRUE"), _observation("B", "FALSE")])
  _protocol(data)["requirements"] = [
    {"id": "req", "description": "x", "expr": ATOM_B, "addressed": {"const": True}}
  ]

  report = _audit_data(data)

  assert _verdict_status(report) == "ENTAILED"
  assert report["procedural_adherence"] == "VIOLATED"


def test_user_atom_named_like_the_verdict_keeps_its_own_witness() -> None:
  data = _minimal_case(findings=[_observation("A", "TRUE")])
  json_list(_protocol(data)["atoms"]).append(
    {"id": "PASS", "role": "judge", "description": "user atom PASS"}
  )
  _findings(data).append(_observation("PASS", "FALSE"))

  verdict = present(_audit_data(data)["rationale_verdict"])

  assert verdict.get("witnesses", {})["true"]["PASS"] is False


def test_gold_verdict_is_not_solver_input() -> None:
  data = replay_object()
  before = dict(_audit_data(data))
  data["gold_verdict"] = "FAIL"
  after = dict(_audit_data(data))

  assert before["verdict_correct"] != after["verdict_correct"]
  for report in (before, after):
    del report["gold_verdict"]
    del report["verdict_correct"]
  assert before == after


def test_external_violation_does_not_rewrite_the_decision() -> None:
  data = replay_object()
  _finding_for(data, "external")["value"] = "TRUE"

  report = _audit_data(data)

  assert report["procedural_adherence"] == "VIOLATED"
  assert _verdict_status(report) == "ENTAILED"


def test_missing_fact_is_unknown_not_false() -> None:
  data = replay_object()
  data["findings"] = [
    item for item in _findings(data) if json_object(item)["atom"] != "external"
  ]

  assert _audit_data(data)["procedural_adherence"] == "UNKNOWN"


def test_invalid_and_unknown_translations_are_reported() -> None:
  data = replay_object("invalid")
  assert _audit_data(data)["status"] == "TRANSLATION_INVALID"

  json_object(data["extraction"])["translation_status"] = "UNKNOWN"

  assert _audit_data(data)["status"] == "TRANSLATION_UNKNOWN"


def test_omitted_dimensions_prevent_a_positive_overall_result() -> None:
  data = replay_object()
  _protocol(data)["requirements"] = []
  data["grounding_pairs"] = []

  report = _audit_data(data)

  assert report["procedural_adherence"] == "UNKNOWN"
  assert report.get("grounding_correctness") == "UNKNOWN"
  assert report["protocol_adherent"] == "UNKNOWN"


def _random_expression(rng: random.Random, depth: int) -> dict[str, object]:
  if depth == 0 or rng.random() < 0.3:
    return {"atom": rng.choice(["A", "B"])}
  operator = rng.choice(["and", "or", "not", "implies", "iff"])
  arity = 1 if operator == "not" else 2
  return _expression(
    operator, *(_random_expression(rng, depth - 1) for _ in range(arity))
  )


def _evaluate(expression: dict[str, object], assignment: dict[str, bool]) -> bool:
  if "atom" in expression:
    return assignment[json_text(expression["atom"])]
  if "const" in expression:
    return bool(expression["const"])
  operands = [
    _evaluate(json_object(item), assignment) for item in json_list(expression["args"])
  ]
  operator = expression["op"]
  if operator == "and":
    return all(operands)
  if operator == "or":
    return any(operands)
  if operator == "not":
    return not operands[0]
  if operator == "implies":
    return not operands[0] or operands[1]
  if operator == "iff":
    return operands[0] == operands[1]
  raise ValueError(f"unexpected operator {operator!r}")


def _expected_status(outcomes: list[bool], observed_pass: bool) -> str:
  if not outcomes:
    return "INCONSISTENT_BASE"
  if all(outcome == observed_pass for outcome in outcomes):
    return "ENTAILED"
  if all(outcome != observed_pass for outcome in outcomes):
    return "REFUTED"
  return "UNDERDETERMINED"


def test_random_formulas_match_exhaustive_truth_tables() -> None:
  rng = random.Random(491)

  for _ in range(150):
    decision = _random_expression(rng, 3)
    constraint = _random_expression(rng, 2)
    observations = {name: rng.choice(["TRUE", "FALSE", "UNKNOWN"]) for name in "AB"}
    outcomes: list[bool] = []
    for a_value, b_value in itertools.product([False, True], repeat=2):
      assignment = {"A": a_value, "B": b_value}
      consistent = all(
        value == "UNKNOWN" or assignment[name] == (value == "TRUE")
        for name, value in observations.items()
      )
      if consistent and _evaluate(constraint, assignment):
        outcomes.append(_evaluate(decision, assignment))
    data = _minimal_case(
      decision, [_observation(name, value) for name, value in observations.items()]
    )
    _protocol(data)["constraints"] = [
      {"id": "c", "description": "x", "expr": constraint}
    ]
    observed = rng.choice(["PASS", "FAIL"])
    data["verdict"] = observed

    report = _audit_data(data)

    assert _verdict_status(report) == _expected_status(outcomes, observed == "PASS")


# --- Studio wire reports ----------------------------------------------------


def test_replay_document_and_hash_contract() -> None:
  text = replay_text()
  case = load_json_object(text)
  binding = {"case": case, "auditorVersion": "1", "schemaVersion": 1}
  expected_hash = hashlib.sha256(
    json.dumps(binding, sort_keys=True, ensure_ascii=False).encode("utf-8")
  ).hexdigest()

  result = validate_audit_case(text)

  assert result["contentHash"] == expected_hash
  assert result["documents"] == [
    {
      "id": role,
      "role": role,
      "name": name,
      "mediaType": "text/plain",
      "rawText": raw_text,
      "structuralUnits": [],
    }
    for role, name, raw_text in (
      ("protocol", "Protocol", _protocol(case)["text"]),
      ("context", "Context", case["context"]),
      ("response", "Candidate response", case["response"]),
      ("rationale", "Judge rationale", case["rationale"]),
    )
  ]
  assert validate_audit_case(json.dumps(case, indent=4))["contentHash"] == expected_hash


def test_case_hash_binds_rationale_and_annotations() -> None:
  text = replay_text()
  original_hash = validate_audit_case(text)["contentHash"]

  changed_rationale = load_json_object(text)
  changed_rationale["rationale"] = json_text(changed_rationale["rationale"]) + " Extra."
  changed_annotation = load_json_object(text)
  json_object(_findings(changed_annotation)[0])["value"] = "UNKNOWN"

  assert (
    validate_audit_case(json.dumps(changed_rationale))["contentHash"] != original_hash
  )
  assert (
    validate_audit_case(json.dumps(changed_annotation))["contentHash"] != original_hash
  )


def test_validation_adapter_rejects_an_invalid_input_type_clearly() -> None:
  with pytest.raises(ValidationError, match="VerificationCase"):
    _ = validate_audit_case(cast("VerificationCase", cast("object", {})))


@pytest.mark.parametrize(
  "prefix,status",
  [
    ("unknown", "OK"),
    ("contradictory", "INCONSISTENT_PROTOCOL"),
    ("invalid", "TRANSLATION_INVALID"),
  ],
)
def test_completed_reports_preserve_nonpositive_states(
  prefix: str, status: str
) -> None:
  report = audit_judge_case(replay_text(prefix))

  summary = json_object(report["summary"])
  assert report["status"] == "completed"
  assert summary["status"] == status
  assert summary["overall"] == "UNKNOWN"


def test_report_source_references_preserve_exact_evidence() -> None:
  report = audit_judge_case(replay_text())
  sources = {
    json_text(json_object(item)["id"]): json_text(json_object(item)["rawText"])
    for item in json_list(report["documents"])
  }

  assert set(sources) == {"protocol", "context", "response", "rationale"}
  for check in json_list(report["checks"]):
    for item in json_list(json_object(check)["sourceReferences"]):
      reference = json_object(item)
      start = json_integer(reference["start"])
      end = json_integer(reference["end"])
      source = sources[json_text(reference["documentId"])]
      assert source[start:end] == reference["quote"]


@pytest.mark.parametrize("addressed,expected", [(False, "VIOLATED"), (None, "UNKNOWN")])
def test_procedural_summary_matches_core_when_coverage_is_not_established(
  addressed: bool | None, expected: str
) -> None:
  data = replay_object()
  requirement: dict[str, object] = {
    "id": "observation",
    "description": "Check observable coverage",
    "expr": {"const": True},
  }
  if addressed is not None:
    requirement["addressed"] = {"const": addressed}
  _protocol(data)["requirements"] = [requirement]

  report = audit_judge_case(json.dumps(data))

  assert json_object(report["audit"])["procedural_adherence"] == expected
  assert json_object(report["summary"])["procedure"] == expected


def test_invalid_unicode_is_rejected_before_solver_work(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  data = replay_object()
  data["context"] = json_text(data["context"]) + chr(0xD800)

  def fail_if_solved(_case: VerificationCase) -> AuditReport:
    pytest.fail("invalid Unicode reached solver")

  monkeypatch.setattr("unmask.judge.audit", fail_if_solved)

  with pytest.raises(ValidationError, match="invalid Unicode"):
    _ = audit_judge_case(json.dumps(data))


def test_concurrent_audits_use_independent_solver_contexts() -> None:
  with ThreadPoolExecutor(max_workers=2) as executor:
    reports = list(
      executor.map(audit_judge_case, [replay_text(), replay_text("unknown")])
    )

  assert json_object(reports[0]["summary"])["entailment"] == "ENTAILED"
  assert json_object(reports[1]["summary"])["entailment"] == "UNDERDETERMINED"
