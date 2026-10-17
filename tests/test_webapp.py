import json
import re
from html.parser import HTMLParser
from importlib.metadata import PackageNotFoundError
from threading import BoundedSemaphore
from typing import cast, override

import pytest
import yaml
import z3
from flask import Flask
from werkzeug.datastructures import Headers
from werkzeug.test import TestResponse

from tests.helpers import (
  EXAMPLES,
  json_object,
  replay_object,
  replay_text,
  response_object,
)
from unmask import webapp as webapp_module
from unmask.backend import JudgeBackend
from unmask.config import TargetConfig
from unmask.judge import validate_audit_case
from unmask.repair import (
  FindingFingerprint,
  ProposedFix,
  ProposedFixStatus,
  RepairGenerationError,
  ValidationCheck,
  ValidationStatus,
)
from unmask.webapp import MAX_REQUEST_BYTES, create_app, run_local_server


@pytest.fixture
def application() -> Flask:
  app: Flask = create_app()
  app.config["TESTING"] = True
  return app


class _IndexMarkup(HTMLParser):
  def __init__(self) -> None:
    super().__init__()
    self.element_ids: set[str] = set()
    self.asset_paths: list[str] = []

  @override
  def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
    attributes = dict(attrs)
    identifier = attributes.get("id")
    if identifier:
      self.element_ids.add(identifier)
    reference = attributes.get("src") if tag == "script" else attributes.get("href")
    if reference and reference.startswith("/assets/"):
      self.asset_paths.append(reference)


def _fetch(application: Flask, path: str) -> tuple[int, Headers, str]:
  with application.test_client().get(path) as response:
    return response.status_code, response.headers, response.get_data(as_text=True)


def _studio_index(application: Flask) -> _IndexMarkup:
  _, _, markup_text = _fetch(application, "/")
  markup = _IndexMarkup()
  markup.feed(markup_text)
  return markup


def test_index_serves_studio_with_security_headers(application: Flask) -> None:
  status, headers, _ = _fetch(application, "/")

  assert status == 200
  assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
  assert "no-store" in headers["Cache-Control"]


def test_index_assets_are_served_without_caching(application: Flask) -> None:
  paths = _studio_index(application).asset_paths

  assert any(path.startswith("/assets/app.js") for path in paths)
  assert any(path.startswith("/assets/style.css") for path in paths)
  for path in paths:
    status, headers, _ = _fetch(application, path)
    assert status == 200, path
    assert "no-store" in headers["Cache-Control"], path


def test_scripts_only_look_up_elements_present_in_the_index(
  application: Flask,
) -> None:
  markup = _studio_index(application)
  looked_up: set[str] = set()
  for path in markup.asset_paths:
    if ".js" not in path:
      continue
    _, _, script = _fetch(application, path)
    looked_up.update(re.findall(r'element\("([\w-]+)"\)', script))

  assert looked_up
  assert looked_up <= markup.element_ids


def test_scripts_never_assign_markup_strings(application: Flask) -> None:
  for path in _studio_index(application).asset_paths:
    if ".js" not in path:
      continue
    _, _, script = _fetch(application, path)
    assert "innerHTML" not in script, path


def test_capabilities_never_expose_credential_values(
  application: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setenv("OPENAI_API_KEY", "secret-value")

  response = application.test_client().get("/api/v1/capabilities")
  payload = cast("dict[str, object]", response.get_json())

  assert response.status_code == 200
  assert "secret-value" not in response.text
  assert payload["storage"] == "browser_local"
  assert cast("dict[str, object]", payload["proposedFixes"])["enabled"] is True
  assert isinstance(payload["version"], str)
  providers = cast("list[dict[str, object]]", payload["providers"])
  for provider in providers:
    models = cast("list[str]", provider["models"])
    assert provider["defaultModel"] in models


def test_samples_are_packaged_and_loadable(application: Flask) -> None:
  response = application.test_client().get("/api/v1/samples")
  payload = cast("dict[str, list[dict[str, str]]]", response.get_json())

  assert response.status_code == 200
  assert {sample["id"] for sample in payload["samples"]} == {
    "guardrail",
    "regexes",
  }


def test_validate_returns_source_aware_document(application: Flask) -> None:
  text = "name: sample\nsystem_prompt: help\nrules:\n  - Block harm.\n"

  response = application.test_client().post(
    "/api/v1/validate",
    json={"specification": {"name": "sample.yaml", "text": text}},
  )
  payload = cast("dict[str, object]", response.get_json())

  assert response.status_code == 200
  assert payload["specificationKind"] == "natural_language"
  assert "Block harm." in response.text


@pytest.mark.parametrize(
  "payload",
  [
    {"specification": {"name": "x.yaml", "text": "name: ["}},
    {"specification": {"name": "x.yaml", "text": "kind: unknown"}},
    {"specification": {"name": "x.yaml", "text": "rules: []"}, "extra": True},
  ],
)
def test_validate_returns_structured_errors(
  application: Flask, payload: dict[str, object]
) -> None:
  response = application.test_client().post("/api/v1/validate", json=payload)

  assert response.status_code == 400
  assert cast("dict[str, object]", response.get_json())["error"]


def test_validate_rejects_non_json_with_structured_error(application: Flask) -> None:
  response = application.test_client().post(
    "/api/v1/validate", data="plain text", content_type="text/plain"
  )

  assert response.status_code == 400
  assert "valid JSON" in response.text


class FakeScanResult:
  def to_dict(self) -> dict[str, object]:
    return {
      "schemaVersion": 1,
      "status": "completed",
      "workflow": "scan",
      "specificationKind": "natural_language",
      "summary": {"status": "completed"},
      "findings": [],
    }


def test_scan_passes_selected_thinking_budget_to_service(
  application: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
  targets: list[TargetConfig] = []

  def fake_bugs(
    specification_text: str,
    *,
    specification_name: str,
    taxonomy_text: str | None,
    model: TargetConfig,
  ) -> FakeScanResult:
    assert specification_text
    assert specification_name == "sample.yaml"
    assert taxonomy_text is None
    targets.append(model)
    return FakeScanResult()

  monkeypatch.setattr(webapp_module, "bugs", fake_bugs)
  response = application.test_client().post(
    "/api/v1/scans",
    json={
      "specification": {
        "name": "sample.yaml",
        "text": "name: sample\nsystem_prompt: help\nrules:\n  - Block harm.\n",
      },
      "provider": "gemini",
      "model": "gemini-test",
      "thinkingBudget": 70,
    },
  )

  assert response.status_code == 200
  assert len(targets) == 1
  assert targets[0].thinking_budget == 70


@pytest.mark.parametrize("thinking_budget", [-1, 1.5, True])
def test_scan_rejects_invalid_thinking_budget(
  application: Flask, thinking_budget: object
) -> None:
  response = application.test_client().post(
    "/api/v1/scans",
    json={
      "specification": {
        "name": "sample.yaml",
        "text": "name: sample\nsystem_prompt: help\nrules:\n  - Block harm.\n",
      },
      "thinkingBudget": thinking_budget,
    },
  )

  assert response.status_code == 400
  assert "thinkingBudget must be a non-negative integer" in response.text


def test_regex_scan_runs_without_external_provider(application: Flask) -> None:
  text = (
    "kind: regex\nname: sample\nengine: python_re\nscope: agent_output\n"
    "rules:\n  - id: block-hack\n    action: block\n    pattern: hack\n"
    "examples:\n  - content: harmless hack report\n    expected_action: allow\n"
    "    reason: neutral reporting\n"
  )

  response = application.test_client().post(
    "/api/v1/scans",
    json={"specification": {"name": "sample.yaml", "text": text}},
  )
  payload = cast("dict[str, object]", response.get_json())

  assert response.status_code == 200
  assert payload["specificationKind"] == "regex"
  assert '"sourceReferences"' in response.text


def test_regex_diagnosis_runs_without_external_provider(application: Flask) -> None:
  specification = (EXAMPLES / "regexes.yaml").read_text(encoding="utf-8")
  violations = (EXAMPLES / "violations.json").read_text(encoding="utf-8")

  response = application.test_client().post(
    "/api/v1/diagnoses",
    json={
      "specification": {"name": "regexes.yaml", "text": specification},
      "violations": {"name": "violations.json", "text": violations},
    },
  )
  payload = cast("dict[str, object]", response.get_json())

  assert response.status_code == 200
  assert payload["workflow"] == "diagnose"
  assert payload["specificationKind"] == "regex"
  assert cast("dict[str, object]", payload["summary"])["incidents"] == 3
  assert payload["findings"]


def test_proposed_fix_endpoint_passes_bound_inputs(
  application: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
  received: list[dict[str, object]] = []

  def fake_propose_fix(specification_text: str, **kwargs: object) -> ProposedFix:
    received.append({"specification_text": specification_text, **kwargs})
    target = cast("TargetConfig", kwargs["target"])
    return ProposedFix(
      status=ProposedFixStatus.VALIDATED,
      specification_kind="regex",
      finding_fingerprint=FindingFingerprint(
        specification_kind="regex",
        workflow="scan",
        bug_type="over_strict",
        rule_ids=("block-hack",),
        finding_id="case:1:over-strict:block-hack",
      ),
      original_content_hash="bound-hash",
      provider=target.provider,
      model=target.judge_model,
      revised_specification="kind: regex\n",
      unified_diff="--- sample.yaml\n+++ sample.yaml\n",
      validation_checks=(
        ValidationCheck(
          code="target_removed",
          label="Target finding removed",
          status=ValidationStatus.PASSED,
        ),
      ),
    )

  monkeypatch.setattr(webapp_module, "propose_fix", fake_propose_fix)
  response = application.test_client().post(
    "/api/v1/proposed-fixes",
    json={
      "specification": {"name": "sample.yaml", "text": "kind: regex\n"},
      "workflow": "scan",
      "findingId": "case:1:over-strict:block-hack",
      "contentHash": "bound-hash",
      "provider": "openai",
      "model": "gpt-test",
      "thinkingBudget": 70,
    },
  )
  payload = cast("dict[str, object]", response.get_json())

  assert response.status_code == 200
  assert payload["status"] == "validated"
  assert received[0]["expected_content_hash"] == "bound-hash"
  target = cast("TargetConfig", received[0]["target"])
  assert (target.provider, target.judge_model, target.thinking_budget) == (
    "openai",
    "gpt-test",
    70,
  )


def test_proposed_fix_endpoint_reports_stale_analysis(
  application: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
  def stale(*args: object, **kwargs: object) -> ProposedFix:
    del args, kwargs
    raise LookupError("analysis inputs changed")

  monkeypatch.setattr(webapp_module, "propose_fix", stale)
  response = application.test_client().post(
    "/api/v1/proposed-fixes",
    json={
      "specification": {"name": "sample.yaml", "text": "kind: regex\n"},
      "workflow": "scan",
      "findingId": "finding",
      "contentHash": "old",
    },
  )

  assert response.status_code == 409
  assert "stale_analysis" in response.text


@pytest.mark.parametrize(
  "headers",
  [
    {"Origin": "https://attacker.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
  ],
)
def test_proposed_fix_endpoint_rejects_cross_origin_requests(
  application: Flask, headers: dict[str, str]
) -> None:
  response = application.test_client().post(
    "/api/v1/proposed-fixes", json={}, headers=headers
  )

  assert response.status_code == 403
  assert "invalid_origin" in response.text


def test_studio_rejects_untrusted_host(application: Flask) -> None:
  response = application.test_client().get("/", headers={"Host": "attacker.example"})

  assert response.status_code == 400


def test_proposed_fix_endpoint_limits_concurrent_generation(application: Flask) -> None:
  slots = cast("BoundedSemaphore", application.extensions["unmask_proposed_fix_slots"])
  assert slots.acquire(blocking=False)
  try:
    response = application.test_client().post(
      "/api/v1/proposed-fixes",
      json={
        "specification": {"name": "sample.yaml", "text": "kind: regex\n"},
        "workflow": "scan",
        "findingId": "finding",
        "contentHash": "hash",
      },
    )
  finally:
    slots.release()

  assert response.status_code == 429
  assert "proposed_fix_busy" in response.text


def test_diagnosis_rejects_natural_language_specifications(
  application: Flask,
) -> None:
  response = application.test_client().post(
    "/api/v1/diagnoses",
    json={
      "specification": {
        "name": "guardrail.yaml",
        "text": "name: sample\nsystem_prompt: help\nrules:\n  - Block harm.\n",
      },
      "violations": {
        "name": "violations.json",
        "text": (
          '{"violations":[{"context":"hack now","location":0,'
          + '"value":"hack","expected_outcome":"allow",'
          + '"expected_reason":"neutral"}]}'
        ),
      },
    },
  )

  assert response.status_code == 400
  assert "regex specification" in response.text


def test_rejects_oversized_request(application: Flask) -> None:
  response = application.test_client().post(
    "/api/v1/validate",
    data=b"x" * (MAX_REQUEST_BYTES + 1),
    content_type="application/json",
  )

  assert response.status_code == 413
  assert "request_too_large" in response.text


class FakeServer:
  server_port: int = 43123

  def __init__(self) -> None:
    self.served: bool = False
    self.shutdown_called: bool = False
    self.closed: bool = False

  def serve_forever(self) -> None:
    self.served = True

  def shutdown(self) -> None:
    self.shutdown_called = True

  def server_close(self) -> None:
    self.closed = True


def test_local_server_binds_loopback_opens_browser_and_closes() -> None:
  server = FakeServer()
  factory_calls: list[tuple[str, int]] = []
  opened: list[str] = []

  def server_factory(host: str, port: int, application: Flask) -> FakeServer:
    assert isinstance(application, Flask)
    factory_calls.append((host, port))
    return server

  def browser_opener(url: str) -> bool:
    opened.append(url)
    return True

  exit_code = run_local_server(
    port=0,
    browser_opener=browser_opener,
    server_factory=server_factory,
  )

  assert exit_code == 0
  assert factory_calls == [("127.0.0.1", 0)]
  assert opened == ["http://127.0.0.1:43123/"]
  assert server.served and server.shutdown_called and server.closed


def test_browser_launch_failure_does_not_stop_server(
  capsys: pytest.CaptureFixture[str],
) -> None:
  server = FakeServer()

  def server_factory(host: str, port: int, application: Flask) -> FakeServer:
    del host, port, application
    return server

  def browser_opener(url: str) -> bool:
    del url
    raise OSError("browser unavailable")

  exit_code = run_local_server(
    browser_opener=browser_opener,
    server_factory=server_factory,
  )

  assert exit_code == 0
  assert server.served
  assert "warning: could not open" in capsys.readouterr().err


AUDIT_ROUTES = ["/api/v1/judge-audits", "/api/v1/judge-audits/validate"]


def _audit_error_code(response: TestResponse) -> object:
  return json_object(response_object(response)["error"])["code"]


def test_audit_example_validates_without_a_provider(
  application: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:

  def fail_on_provider(config: TargetConfig) -> JudgeBackend:
    pytest.fail(f"unexpected provider call for {config.judge_model}")

  monkeypatch.setattr("unmask.backend.create_backend", fail_on_provider)
  client = application.test_client()

  example = response_object(client.get("/api/v1/judge-audits/example"))
  response = client.post("/api/v1/judge-audits/validate", json={"case": example})

  assert response.status_code == 200
  assert response_object(response)["workflow"] == "audit_judge"


def test_audit_rejects_a_stale_case_before_solving(
  application: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:

  def fail_if_solved(_text: str) -> dict[str, object]:
    pytest.fail("stale case was solved")

  monkeypatch.setattr("unmask.webapp.audit_judge_case", fail_if_solved)

  response = application.test_client().post(
    "/api/v1/judge-audits",
    json={"case": {"name": "case.json", "text": replay_text()}, "contentHash": "old"},
  )

  assert response.status_code == 409
  assert _audit_error_code(response) == "stale_audit_case"


@pytest.mark.parametrize("route", AUDIT_ROUTES)
def test_audit_reports_invalid_unicode_as_a_validation_error(
  application: Flask, route: str
) -> None:
  case = replay_object()
  case["context"] = f"{case['context']}{chr(0xD800)}"

  response = application.test_client().post(
    route, json={"case": {"text": json.dumps(case)}}
  )

  assert response.status_code == 400
  assert _audit_error_code(response) == "invalid_audit_case"
  assert "invalid Unicode" in str(
    json_object(response_object(response)["error"])["message"]
  )


def test_audit_reports_an_invalid_case_shape_as_a_structured_error(
  application: Flask,
) -> None:
  response = application.test_client().post(
    "/api/v1/judge-audits/validate",
    json={"case": {"name": "case.json", "text": "{}"}},
  )

  assert response.status_code == 400
  assert _audit_error_code(response) == "invalid_audit_case"


@pytest.mark.parametrize("route", AUDIT_ROUTES)
@pytest.mark.parametrize(
  "headers",
  [
    {"Origin": "https://attacker.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
  ],
)
def test_audit_rejects_cross_origin_requests(
  application: Flask, route: str, headers: dict[str, str]
) -> None:
  response = application.test_client().post(route, json={}, headers=headers)

  assert response.status_code == 403
  assert _audit_error_code(response) == "invalid_origin"


@pytest.mark.parametrize("route", AUDIT_ROUTES)
def test_audit_requires_a_json_content_type(application: Flask, route: str) -> None:
  response = application.test_client().post(
    route,
    data=json.dumps({"case": {"text": replay_text()}}),
    content_type="text/plain",
  )

  assert response.status_code == 400
  assert _audit_error_code(response) == "invalid_audit_case"


def test_audit_reports_busy_and_preserves_the_slot(application: Flask) -> None:
  slots = cast("BoundedSemaphore", application.extensions["unmask_audit_slots"])
  assert slots.acquire(blocking=False)
  try:
    response = application.test_client().post("/api/v1/judge-audits", json={})

    assert response.status_code == 409
    assert _audit_error_code(response) == "audit_busy"
    assert not slots.acquire(blocking=False)
  finally:
    slots.release()


def test_solver_failure_is_structured_and_releases_the_slot(
  application: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
  def fail_solver(_case: object, _timeout_ms: int) -> object:
    raise z3.Z3Exception("injected solver failure")

  monkeypatch.setattr("unmask.judge.audit", fail_solver)

  response = application.test_client().post(
    "/api/v1/judge-audits",
    json={
      "case": {"name": "case.json", "text": replay_text()},
      "contentHash": validate_audit_case(replay_text())["contentHash"],
    },
  )

  assert response.status_code == 422
  assert _audit_error_code(response) == "audit_failed"
  assert "injected solver failure" not in response.text
  slots = cast("BoundedSemaphore", application.extensions["unmask_audit_slots"])
  assert slots.acquire(blocking=False)
  slots.release()


SAMPLE_TEXT = "name: sample\nsystem_prompt: help\nrules:\n  - Block harm.\n"


def _specification(**overrides: object) -> dict[str, object]:
  document: dict[str, object] = {"name": "sample.yaml", "text": SAMPLE_TEXT}
  document.update(overrides)
  return document


def _error_message(response: TestResponse) -> str:
  return str(json_object(response_object(response)["error"])["message"])


@pytest.mark.parametrize(
  "payload,message",
  [
    ([1], "request body must be a JSON object"),
    ({"specification": "text"}, "specification must be an object"),
    ({"specification": _specification(name=" ")}, "name must be a non-empty string"),
    ({"specification": _specification(text=" ")}, "text must be a non-empty string"),
    ({"specification": _specification(extra=1)}, "unknown request fields: extra"),
    ({"specification": _specification(), "taxonomy": 3}, "taxonomy must be an object"),
    (
      {"specification": _specification(), "provider": "other"},
      "unsupported provider: 'other'",
    ),
    ({"specification": _specification(), "model": " "}, "model must be a non-empty"),
  ],
)
def test_scan_rejects_invalid_request_documents(
  application: Flask, payload: object, message: str
) -> None:
  response = application.test_client().post("/api/v1/scans", json=payload)

  assert response.status_code == 400
  assert json_object(response_object(response)["error"])["code"] == "invalid_scan"
  assert message in _error_message(response)


@pytest.mark.parametrize(
  "route,patched,code",
  [
    ("/api/v1/scans", "bugs", "scan_failed"),
    ("/api/v1/diagnoses", "diagnose_specification", "diagnosis_failed"),
  ],
)
def test_analysis_runtime_failures_are_structured(
  application: Flask,
  monkeypatch: pytest.MonkeyPatch,
  route: str,
  patched: str,
  code: str,
) -> None:
  def fail(*_arguments: object, **_keywords: object) -> object:
    raise RuntimeError("provider unavailable")

  monkeypatch.setattr(webapp_module, patched, fail)

  payload: dict[str, object] = {"specification": _specification()}
  if route.endswith("diagnoses"):
    payload["violations"] = {"name": "violations.json", "text": "{}"}

  response = application.test_client().post(route, json=payload)

  assert response.status_code == 422
  assert json_object(response_object(response)["error"])["code"] == code
  assert _error_message(response) == "provider unavailable"


@pytest.mark.parametrize(
  "overrides,message",
  [
    ({"workflow": "other"}, "workflow must be 'scan' or 'diagnose'"),
    ({"findingId": ""}, "findingId must be a non-empty string"),
    ({"contentHash": ""}, "contentHash must be a non-empty string"),
  ],
)
def test_proposed_fix_rejects_invalid_request_fields(
  application: Flask, overrides: dict[str, object], message: str
) -> None:
  payload: dict[str, object] = {
    "specification": _specification(),
    "workflow": "scan",
    "findingId": "finding",
    "contentHash": "hash",
  }
  payload.update(overrides)

  response = application.test_client().post("/api/v1/proposed-fixes", json=payload)

  assert response.status_code == 400
  assert (
    json_object(response_object(response)["error"])["code"] == "invalid_proposed_fix"
  )
  assert message in _error_message(response)


@pytest.mark.parametrize(
  "failure,status,code",
  [
    (RepairGenerationError("bad candidate"), 422, "proposed_fix_failed"),
    (ValueError("bad input"), 400, "invalid_proposed_fix"),
    (yaml.YAMLError("bad yaml"), 400, "invalid_proposed_fix"),
    (RuntimeError("provider unavailable"), 422, "proposed_fix_failed"),
  ],
)
def test_proposed_fix_failures_are_structured_and_release_the_slot(
  application: Flask,
  monkeypatch: pytest.MonkeyPatch,
  failure: Exception,
  status: int,
  code: str,
) -> None:
  def fail(*_arguments: object, **_keywords: object) -> ProposedFix:
    raise failure

  monkeypatch.setattr(webapp_module, "propose_fix", fail)
  slots = cast("BoundedSemaphore", application.extensions["unmask_proposed_fix_slots"])

  response = application.test_client().post(
    "/api/v1/proposed-fixes",
    json={
      "specification": _specification(),
      "workflow": "scan",
      "findingId": "finding",
      "contentHash": "hash",
    },
  )

  assert response.status_code == status
  assert json_object(response_object(response)["error"])["code"] == code
  assert slots.acquire(blocking=False)
  slots.release()


def test_version_falls_back_when_the_package_is_not_installed(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  def missing(_name: str) -> str:
    raise PackageNotFoundError("unmask")

  monkeypatch.setattr(webapp_module, "version", missing)

  assert webapp_module.application_version() == "development"


def test_refused_browser_launch_warns_and_keeps_serving(
  capsys: pytest.CaptureFixture[str],
) -> None:
  server = FakeServer()

  def server_factory(host: str, port: int, application: Flask) -> FakeServer:
    del host, port, application
    return server

  def browser_opener(url: str) -> bool:
    del url
    return False

  _ = run_local_server(browser_opener=browser_opener, server_factory=server_factory)

  assert server.served
  assert "warning: could not open http://127.0.0.1:43123/" in capsys.readouterr().err


def test_interrupt_stops_the_server_cleanly() -> None:
  class InterruptedServer(FakeServer):
    @override
    def serve_forever(self) -> None:
      raise KeyboardInterrupt

  server = InterruptedServer()

  def server_factory(host: str, port: int, application: Flask) -> FakeServer:
    del host, port, application
    return server

  exit_code = run_local_server(open_browser=False, server_factory=server_factory)

  assert exit_code == 0
  assert server.shutdown_called and server.closed
