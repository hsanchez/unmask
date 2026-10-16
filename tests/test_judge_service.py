"""Replay contract integration and resource-boundary checks."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic

import pytest
import z3

from unmask.cli import main
from unmask.judge import (
  AuditInput,
  ValidationError,
  audit_judge_case,
  validate_audit_case,
)
from unmask.solver import Engine
from unmask.webapp import create_app

FIXTURES = Path(__file__).parent / "fixtures/judge_audit"


def replay(prefix: str = "A_") -> str:
  return next(FIXTURES.glob(f"{prefix}*.json")).read_text(encoding="utf-8")


def test_replay_document_and_hash_contract() -> None:
  text = replay()
  case = json.loads(text)
  result = validate_audit_case(text)
  binding = {"case": case, "auditorVersion": "1", "schemaVersion": 1}
  expected_hash = hashlib.sha256(
    json.dumps(binding, sort_keys=True, ensure_ascii=False).encode("utf-8")
  ).hexdigest()
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
      ("protocol", "Protocol", case["protocol"]["text"]),
      ("context", "Context", case["context"]),
      ("response", "Candidate response", case["response"]),
      ("rationale", "Judge rationale", case["rationale"]),
    )
  ]
  assert validate_audit_case(json.dumps(case, indent=4))["contentHash"] == expected_hash


def test_cli_service_and_api_have_identical_reports(tmp_path, monkeypatch) -> None:
  source = tmp_path / "case.json"
  output = tmp_path / "report.json"
  source.write_text(replay(), encoding="utf-8")
  monkeypatch.setattr(
    "unmask.cli._load_environment",
    lambda _: pytest.fail("audit loaded provider environment"),
  )
  assert main(["audit-judge", str(source), "--output", str(output)]) == 0
  expected = audit_judge_case(replay())
  assert json.loads(output.read_text()) == expected
  client = create_app().test_client()
  response = client.post(
    "/api/v1/judge-audits",
    json={
      "case": {"name": "case.json", "text": replay()},
      "contentHash": expected["contentHash"],
    },
  )
  assert response.status_code == 200
  assert response.json == expected


@pytest.mark.parametrize(
  "prefix,status",
  [
    ("unknown", "OK"),
    ("contradictory", "INCONSISTENT_PROTOCOL"),
    ("invalid", "TRANSLATION_INVALID"),
  ],
)
def test_completed_reports_preserve_nonpositive_states(prefix, status) -> None:
  report = audit_judge_case(replay(prefix))
  assert report["status"] == "completed"
  assert report["summary"]["status"] == status
  assert report["summary"]["overall"] == "UNKNOWN"


def test_api_rejects_stale_case_before_solving(monkeypatch) -> None:
  monkeypatch.setattr(
    "unmask.webapp.audit_judge_case", lambda _: pytest.fail("stale case was solved")
  )
  response = (
    create_app()
    .test_client()
    .post(
      "/api/v1/judge-audits",
      json={"case": {"name": "case.json", "text": replay()}, "contentHash": "old"},
    )
  )
  assert response.status_code == 409
  assert response.json["error"]["code"] == "stale_audit_case"


def test_case_hash_binds_rationale_and_annotations() -> None:
  text = replay()
  changed = json.loads(text)
  changed["rationale"] += " Extra observation."
  assert (
    validate_audit_case(text)["contentHash"]
    != validate_audit_case(json.dumps(changed))["contentHash"]
  )
  changed = json.loads(text)
  changed["findings"][0]["value"] = "UNKNOWN"
  assert (
    validate_audit_case(text)["contentHash"]
    != validate_audit_case(json.dumps(changed))["contentHash"]
  )


def test_ui_references_preserve_exact_evidence() -> None:
  report = audit_judge_case(replay())
  sources = {item["id"]: item["rawText"] for item in report["documents"]}
  assert set(sources) == {"protocol", "context", "response", "rationale"}
  for check in report["checks"]:
    for reference in check["sourceReferences"]:
      assert (
        sources[reference["documentId"]][reference["start"] : reference["end"]]
        == reference["quote"]
      )


def test_expired_solver_budget_returns_unknown() -> None:
  engine = Engine([], {"a": z3.Bool("a")}, deadline=monotonic() - 1)
  assert engine.query(z3.Bool("a"))["status"] == "SOLVER_UNKNOWN"
  assert engine.reason == "audit time budget exhausted"


def test_expired_solver_budget_emits_query_diagnostics(
  caplog: pytest.LogCaptureFixture,
) -> None:
  _ = Engine([], {"a": z3.Bool("a")}, deadline=monotonic() - 1)
  record = caplog.records[-1]
  assert record.__dict__["solver_query"] == "base"
  assert "audit time budget exhausted" in record.getMessage()


def test_unknown_proposition_queries_emit_separate_diagnostics(
  caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
  symbol = z3.Bool("sensitive_predicate")
  engine = Engine([], {"sensitive_predicate": symbol})
  monkeypatch.setattr(engine.solver, "check", lambda: z3.unknown)
  monkeypatch.setattr(engine.solver, "reason_unknown", lambda: "timeout")
  result = engine.query(symbol)
  assert result["status"] == "SOLVER_UNKNOWN"
  assert result["reason"] == {"true": "timeout", "false": "timeout"}
  assert [record.__dict__["solver_query"] for record in caplog.records] == [
    "true",
    "false",
  ]
  assert all(
    "sensitive_predicate" not in record.getMessage() for record in caplog.records
  )


def test_invalid_unicode_is_rejected_before_solver_work(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  case = json.loads(replay())
  case["context"] += chr(0xD800)
  monkeypatch.setattr(
    "unmask.judge.audit", lambda _: pytest.fail("invalid Unicode reached solver")
  )
  with pytest.raises(ValidationError, match="invalid Unicode"):
    audit_judge_case(json.dumps(case))


@pytest.mark.parametrize(
  "route", ["/api/v1/judge-audits", "/api/v1/judge-audits/validate"]
)
def test_api_reports_invalid_unicode_as_a_validation_error(route: str) -> None:
  case = json.loads(replay())
  case["context"] += chr(0xD800)
  response = (
    create_app().test_client().post(route, json={"case": {"text": json.dumps(case)}})
  )
  assert response.status_code == 400
  assert response.json["error"]["code"] == "invalid_audit_case"
  assert "invalid Unicode" in response.json["error"]["message"]


def test_audits_use_independent_solver_contexts() -> None:
  with ThreadPoolExecutor(max_workers=2) as executor:
    reports = list(executor.map(audit_judge_case, [replay(), replay("unknown")]))
  assert reports[0]["summary"]["entailment"] == "ENTAILED"
  assert reports[1]["summary"]["entailment"] == "UNDERDETERMINED"


def test_api_example_validates_without_provider(monkeypatch) -> None:
  monkeypatch.setattr(
    "unmask.backend.create_backend", lambda _: pytest.fail("unexpected provider call")
  )
  client = create_app().test_client()
  example = client.get("/api/v1/judge-audits/example").json
  response = client.post("/api/v1/judge-audits/validate", json={"case": example})
  assert response.status_code == 200
  assert response.json["workflow"] == "audit_judge"


@pytest.mark.parametrize(
  "text", ['{"id":"one","id":"two"}', '{"id":NaN}', " " * 1_000_001]
)
def test_json_boundary_rejects_invalid_inputs(text) -> None:
  with pytest.raises(ValidationError):
    AuditInput.from_json(text)


def test_invalid_api_shape_is_structured_error() -> None:
  response = (
    create_app()
    .test_client()
    .post(
      "/api/v1/judge-audits/validate",
      json={"case": {"name": "case.json", "text": "{}"}},
    )
  )
  assert response.status_code == 400
  assert response.json["error"]["code"] == "invalid_audit_case"


@pytest.mark.parametrize("addressed,expected", [(False, "VIOLATED"), (None, "UNKNOWN")])
def test_procedural_summary_matches_core_when_coverage_is_not_established(
  addressed: bool | None, expected: str
) -> None:
  data = json.loads(replay())
  requirement = {
    "id": "observation",
    "description": "Check observable coverage",
    "expr": {"const": True},
  }
  if addressed is not None:
    requirement["addressed"] = {"const": addressed}
  data["protocol"]["requirements"] = [requirement]

  report = audit_judge_case(json.dumps(data))

  assert report["audit"]["procedural_adherence"] == expected
  assert report["summary"]["procedure"] == expected


@pytest.mark.parametrize(
  "route", ["/api/v1/judge-audits", "/api/v1/judge-audits/validate"]
)
@pytest.mark.parametrize(
  "headers",
  [
    {"Origin": "https://attacker.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
  ],
)
def test_audit_endpoints_reject_cross_origin_requests(route, headers) -> None:
  response = create_app().test_client().post(route, json={}, headers=headers)

  assert response.status_code == 403
  assert response.json["error"]["code"] == "invalid_origin"


@pytest.mark.parametrize(
  "route", ["/api/v1/judge-audits", "/api/v1/judge-audits/validate"]
)
def test_audit_endpoints_require_json_content_type(route) -> None:
  response = (
    create_app()
    .test_client()
    .post(
      route, data=json.dumps({"case": {"text": replay()}}), content_type="text/plain"
    )
  )

  assert response.status_code == 400
  assert response.json["error"]["code"] == "invalid_audit_case"


def test_audit_endpoint_reports_busy_and_preserves_slot() -> None:
  application = create_app()
  slots = application.extensions["unmask_audit_slots"]
  assert slots.acquire(blocking=False)
  try:
    response = application.test_client().post("/api/v1/judge-audits", json={})
    assert response.status_code == 409
    assert response.json["error"]["code"] == "audit_busy"
    assert not slots.acquire(blocking=False)
  finally:
    slots.release()


def test_solver_failure_is_structured_and_releases_slot(monkeypatch) -> None:
  def fail_solver(*args, **kwargs):
    raise z3.Z3Exception("injected solver failure")

  monkeypatch.setattr("unmask.judge.audit", fail_solver)
  application = create_app()
  response = application.test_client().post(
    "/api/v1/judge-audits",
    json={
      "case": {"name": "case.json", "text": replay()},
      "contentHash": validate_audit_case(replay())["contentHash"],
    },
  )

  assert response.status_code == 422
  assert response.json["error"]["code"] == "audit_failed"
  assert "injected solver failure" not in response.text
  slots = application.extensions["unmask_audit_slots"]
  assert slots.acquire(blocking=False)
  slots.release()
