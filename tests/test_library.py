"""Public Python API contracts and parity with existing entry points."""

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
import z3

import unmask
import unmask.service as service
from tests.helpers import (
  REPLAY_FIXTURES,
  json_object,
  load_json_object,
  present,
  replay_object,
  replay_text,
  response_object,
)
from unmask.backend import JudgeBackend, JudgeResponse
from unmask.cli import main
from unmask.config import TargetConfig, guardrail_from_data
from unmask.diagnosis import RegexManifest
from unmask.judge import audit_judge_case, validate_audit_case
from unmask.models import Guardrail
from unmask.solver import Engine
from unmask.webapp import create_app

REPLAY_FILES = sorted(path.name for path in REPLAY_FIXTURES.glob("*.json"))
REGEX = """kind: regex
name: sample
rules:
  - id: block-hack
    action: block
    pattern: hack
examples:
  - content: harmless hack report
    expected_action: allow
    reason: Neutral reporting.
"""
NATURAL = "name: sample\nsystem_prompt: Be helpful.\nrules:\n  - Block safe requests.\n"


class FakeBackend:
  def judge(self, prompt: str, *, max_tokens: int = 8192) -> JudgeResponse:
    del prompt, max_tokens
    return JudgeResponse(
      json.dumps(
        {
          "predicates": {"safe": "request is safe"},
          "safe_condition": [[{"predicate": "safe", "value": True}]],
          "rules": [
            {
              "rule_id": "rule:1",
              "decision": "block",
              "condition": [[{"predicate": "safe", "value": True}]],
              "rationale": "Blocks safe requests.",
            }
          ],
          "ambiguities": [],
        }
      )
    )


def _fail_on_provider(config: TargetConfig) -> JudgeBackend:
  pytest.fail(f"provider called for {config.judge_model}")


def _fake_backend(config: TargetConfig) -> JudgeBackend:
  del config
  return FakeBackend()


def _fake_model() -> TargetConfig:
  return TargetConfig(
    project=None, location="global", judge_model="fake", thinking_budget=None
  )


def test_regex_bugs_needs_no_provider() -> None:
  result = unmask.bugs(REGEX, backend_factory=_fail_on_provider)
  assert result.specification_kind == "regex"
  assert result.findings
  assert all(finding.summary for finding in result.findings)
  assert result.to_dict() == service.bugs(REGEX).to_dict()


def test_natural_bugs_uses_explicit_model_configuration() -> None:
  result = unmask.bugs(NATURAL, model=_fake_model(), backend_factory=_fake_backend)
  assert [finding.bug_type for finding in result.findings] == ["over_strict"]
  assert result.findings[0].summary


def test_natural_bugs_requires_model_configuration() -> None:
  with pytest.raises(ValueError, match="requires model configuration"):
    _ = unmask.bugs(NATURAL)


def test_model_argument_requires_configuration_not_a_model_name() -> None:
  with pytest.raises(TypeError, match="TargetConfig"):
    _ = unmask.bugs(NATURAL, model=cast("TargetConfig", cast("object", "fake-model")))


def test_json_constructor_requires_a_string() -> None:
  with pytest.raises(unmask.ValidationError, match="string"):
    _ = unmask.VerificationCase.from_json(cast("str", cast("object", {})))


@pytest.mark.parametrize("fixture", REPLAY_FILES)
def test_verification_case_constructor_and_json_are_equivalent(fixture: str) -> None:
  parsed = unmask.VerificationCase.from_json(replay_text(fixture))
  direct = replace(parsed)
  assert parsed == direct
  assert unmask.adherence(parsed) == unmask.adherence(direct)
  assert unmask.VerificationCase.from_json(json.dumps(direct.to_dict())) == direct


def test_direct_case_construction() -> None:
  case = unmask.VerificationCase(
    id="example",
    protocol_text="PASS iff the context supports the response.",
    context="Alice is CTO.",
    response="Alice is CTO.",
    rationale="Supported. PASS.",
    verdict="PASS",
    atoms=(unmask.Atom("supported", "judge", "Judge states support"),),
    decision=unmask.Expr("atom", atom="supported"),
    annotations=(
      unmask.Annotation(
        "supported",
        unmask.Truth.TRUE,
        (unmask.Evidence("rationale", 0, 10, "Supported."),),
        "Judge states support.",
      ),
    ),
    extraction={"provider": "manual", "translation_status": "VALID"},
  )
  result = unmask.adherence(case)
  assert result.report.get("verdict_entailed") is True
  assert result.overall == "UNKNOWN"  # No procedural or grounding checks supplied.
  assert not result.findings
  assert result.checks[0].status == "ENTAILED"


@pytest.mark.parametrize(
  "change",
  [
    {"verdict": "MAYBE"},
    {"atoms": ()},
    {"decision": unmask.Expr("atom", atom="missing")},
    {"decision": unmask.Expr("and")},
    {"decision": unmask.Expr("const", value=cast("bool", cast("object", 1)))},
    {
      "decision": unmask.Expr(
        "and", args=(cast("unmask.Expr", cast("object", {"atom": "j1"})),)
      )
    },
    {"extraction": {"provider": "manual", "translation_status": "bogus"}},
    {"grounding_pairs": (("c1", "j1"),)},
    {"context": "different text"},
    {
      "requirements": (
        unmask.ProtocolRule("bad", "Invalid", unmask.Expr("atom", atom="missing")),
      )
    },
  ],
)
def test_direct_construction_enforces_replay_invariants(
  change: dict[str, object],
) -> None:
  case = unmask.VerificationCase.from_json(replay_text())
  with pytest.raises(unmask.ValidationError):
    _ = replace(case, **change)


def test_case_metadata_is_detached_and_immutable() -> None:
  case = unmask.VerificationCase.from_json(replay_text())
  metadata = {"provider": "manual", "translation_status": "VALID"}
  direct = replace(case, extraction=metadata)
  metadata["translation_status"] = "INVALID"
  assert direct.extraction["translation_status"] == "VALID"
  with pytest.raises(TypeError):
    cast("dict[str, str]", direct.extraction)["translation_status"] = "INVALID"


def test_direct_expression_depth_is_bounded() -> None:
  case = unmask.VerificationCase.from_json(replay_text())
  expression = unmask.Expr("atom", atom="j1")
  for _ in range(18):
    expression = unmask.Expr("not", args=(expression,))
  with pytest.raises(unmask.ValidationError, match="resource limit"):
    _ = replace(case, decision=expression)


def test_typed_expression_validation_reuses_the_existing_tree() -> None:
  child = unmask.Expr("atom", atom="supported")
  expression = unmask.Expr("not", args=(child,))
  budget = [0]
  assert unmask.Expr.parse(expression, {"supported"}, budget) is expression
  assert expression.args[0] is child
  assert budget == [2]


def test_adherence_preserves_solver_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
  def unknown_check(_engine: Engine, _query: str = "base") -> object:
    return z3.unknown

  monkeypatch.setattr(Engine, "_check", unknown_check)
  result = unmask.adherence(unmask.VerificationCase.from_json(replay_text()))
  assert result.status == "SOLVER_UNKNOWN"
  assert result.overall == "UNKNOWN"
  assert not result.findings
  assert all(check.status == "SOLVER_UNKNOWN" for check in result.checks)


def test_case_payload_is_detached_from_export_mutation() -> None:
  case = unmask.VerificationCase.from_json(replay_text())
  exported = case.to_dict()
  exported["rationale"] = "tampered"
  assert case.to_dict() == replay_object()


def test_direct_evidence_source_requires_text() -> None:
  case = unmask.VerificationCase.from_json(replay_text())
  annotation = case.annotations[0]
  evidence = replace(annotation.evidence[0], source=cast("str", cast("object", [])))
  with pytest.raises(unmask.ValidationError):
    _ = replace(
      case,
      annotations=(replace(annotation, evidence=(evidence,)), *case.annotations[1:]),
    )


def test_constraint_coverage_remains_invalid_for_both_construction_paths() -> None:
  case = unmask.VerificationCase.from_json(replay_text())
  data = replay_object()
  json_object(data["protocol"])["constraints"] = [
    {
      "id": "constraint",
      "description": "Always applicable",
      "expr": {"const": True},
      "addressed": {"const": True},
    }
  ]
  with pytest.raises(unmask.ValidationError, match="unexpected fields"):
    _ = unmask.VerificationCase.from_json(json.dumps(data))
  constraint = unmask.ProtocolRule(
    "constraint",
    "Always applicable",
    unmask.Expr("const", value=True),
    unmask.Expr("const", value=True),
  )
  with pytest.raises(
    unmask.ValidationError, match="constraints cannot define coverage"
  ):
    _ = replace(case, constraints=(constraint,))


@pytest.mark.parametrize(
  "fixture",
  [
    "B_correct_procedurally_nonadherent.json",
    "D_incorrect_procedurally_nonadherent.json",
    "contradictory_protocol.json",
  ],
)
def test_findings_are_detected_defects_not_annotations(fixture: str) -> None:
  result = unmask.adherence(unmask.VerificationCase.from_json(replay_text(fixture)))
  assert result.findings
  assert all(finding.summary for finding in result.findings)
  assert all(
    finding.check is None or finding.check.status == "REFUTED"
    for finding in result.findings
  )
  assert result.annotations == tuple(result.report["findings"])


@pytest.mark.parametrize(
  "fixture", ["unknown_support.json", "invalid_translation.json"]
)
def test_empty_findings_do_not_imply_adherence(fixture: str) -> None:
  result = unmask.adherence(unmask.VerificationCase.from_json(replay_text(fixture)))
  assert result.overall == "UNKNOWN"
  assert result.limitations


def test_adherence_validates_its_boundary() -> None:
  with pytest.raises(TypeError, match="VerificationCase"):
    _ = unmask.adherence(cast("unmask.VerificationCase", cast("object", {})))
  with pytest.raises(unmask.ValidationError, match="timeout"):
    _ = unmask.adherence(unmask.VerificationCase.from_json(replay_text()), timeout_ms=0)


def test_web_execution_decodes_the_case_once(monkeypatch: pytest.MonkeyPatch) -> None:
  expected = validate_audit_case(replay_text())
  original = unmask.VerificationCase.from_json
  calls: list[str] = []

  def parse(text: str) -> unmask.VerificationCase:
    calls.append(text)
    return original(text)

  monkeypatch.setattr(unmask.VerificationCase, "from_json", parse)
  response = (
    create_app()
    .test_client()
    .post(
      "/api/v1/judge-audits",
      json={
        "case": {"name": "case.json", "text": replay_text()},
        "contentHash": expected["contentHash"],
      },
    )
  )
  payload = response_object(response)
  assert response.status_code == 200
  assert calls == [replay_text()]
  result = unmask.adherence(original(replay_text()))
  assert payload["audit"] == result.report
  assert payload["checks"] == [check.to_dict() for check in result.checks]


def test_scan_builds_the_manifest_once(monkeypatch: pytest.MonkeyPatch) -> None:
  original = RegexManifest.from_dict
  calls: list[object] = []

  def parse(data: object) -> RegexManifest:
    calls.append(data)
    return original(data)

  monkeypatch.setattr(RegexManifest, "from_dict", parse)
  _ = unmask.bugs(REGEX)
  assert len(calls) == 1


def test_natural_scan_builds_the_guardrail_once(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls: list[object] = []

  def parse(data: object) -> Guardrail:
    calls.append(data)
    return guardrail_from_data(data)

  monkeypatch.setattr(service, "guardrail_from_data", parse)
  _ = unmask.bugs(NATURAL, model=_fake_model(), backend_factory=_fake_backend)
  assert len(calls) == 1


def test_diagnosis_uses_the_shared_manifest_once(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  original = RegexManifest.from_dict
  calls: list[object] = []

  def parse(data: object) -> RegexManifest:
    calls.append(data)
    return original(data)

  monkeypatch.setattr(RegexManifest, "from_dict", parse)
  result = service.diagnose_specification(
    REGEX,
    json.dumps(
      {
        "violations": [
          {
            "context": "harmless hack report",
            "location": 1,
            "value": "hack",
            "expected_outcome": "allow",
            "expected_reason": "Neutral reporting.",
          }
        ]
      }
    ),
  )
  assert len(calls) == 1
  assert result.diagnosis_reports


def test_cli_library_and_web_produce_identical_judge_reports(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  source = tmp_path / "case.json"
  output = tmp_path / "report.json"
  _ = source.write_text(replay_text(), encoding="utf-8")

  def fail_on_environment(_path: Path) -> None:
    pytest.fail("audit loaded provider environment")

  monkeypatch.setattr("unmask.cli._load_environment", fail_on_environment)

  assert main(["adherence", str(source), "--output", str(output)]) == 0

  expected = audit_judge_case(replay_text())
  assert load_json_object(output.read_text(encoding="utf-8")) == expected
  response = (
    create_app()
    .test_client()
    .post(
      "/api/v1/judge-audits",
      json={
        "case": {"name": "case.json", "text": replay_text()},
        "contentHash": present(expected["contentHash"]),
      },
    )
  )
  assert response.status_code == 200
  assert response_object(response) == expected
