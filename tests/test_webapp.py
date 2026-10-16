from pathlib import Path
from threading import BoundedSemaphore
from typing import cast

import pytest
from flask import Flask
from werkzeug.test import TestResponse

from unmask import webapp as webapp_module
from unmask.config import TargetConfig
from unmask.repair import (
  FindingFingerprint,
  ProposedFix,
  ProposedFixStatus,
  ValidationCheck,
  ValidationStatus,
)
from unmask.webapp import MAX_REQUEST_BYTES, create_app, run_local_server

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _response_data(response: TestResponse) -> bytes:
  return response.get_data()


@pytest.fixture
def application() -> Flask:
  app: Flask = create_app()
  app.config["TESTING"] = True
  return app


def test_index_serves_studio_with_security_headers(application: Flask) -> None:
  response = application.test_client().get("/")
  response_data: bytes = _response_data(response)

  assert response.status_code == 200
  assert b'id="specifications-panel"' in response_data
  assert b'id="analysis-panel"' in response_data
  assert b'id="comparison-tabs"' in response_data
  assert b'id="scan-mode"' in response_data
  assert b'id="diagnose-mode"' in response_data
  assert b"Provider" in response_data
  assert b'id="provider-value"' in response_data
  assert b'id="provider-picker-button"' in response_data
  assert b'id="provider-menu"' in response_data
  assert b"Model" in response_data
  assert b'id="model-value"' in response_data
  assert b'id="model-picker-button"' in response_data
  assert b'id="model-menu"' in response_data
  assert b'id="thinking-budget-input"' in response_data
  assert b'id="taxonomy-toggle"' in response_data
  assert b'id="active-file-control"' in response_data
  assert b'id="restore-samples-button"' in response_data
  assert b'href="https://github.com/hsanchez/unmask#readme"' in response_data
  assert b'class="analysis-actions"' in response_data
  assert b'id="analyze-button"' in response_data
  assert b'href="/assets/style.css?v=22"' in response_data
  assert b'src="/assets/app.js?v=22"' in response_data
  assert b'id="regex-model-note"' in response_data
  assert b'id="workflow-note"' in response_data
  assert b'<label for="audit-paste">Or paste replay JSON</label>' in response_data
  assert b'aria-describedby="audit-import-hint"' in response_data
  assert b'class="audit-import-submit"' in response_data
  assert b'src="/assets/audit.js?v=4"' in response_data
  assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
  assert "no-store" in response.headers["Cache-Control"]


def test_assets_disable_cache_for_local_development(application: Flask) -> None:
  response = application.test_client().get("/assets/app.js")
  response_data: bytes = _response_data(response)

  assert response.status_code == 200
  assert b"indexedDB" in response_data
  assert b"textContent" in response_data
  assert b"innerHTML" not in response_data
  assert b"renderEmptyWorkspace()" in response_data
  assert b"/api/v1/diagnoses" in response_data
  assert b"const modelControlsApply" in response_data
  assert b"elements.providerControl.hidden = false" in response_data
  assert b"navigator.clipboard?.writeText" in response_data
  assert b"setComparisonTab" in response_data
  assert b"toggleTaxonomy" in response_data
  assert b"studyActionIcon" in response_data
  assert b"sample_override" in response_data
  assert b"startRenamingStudy" in response_data
  assert b"finishRenamingStudy" in response_data
  assert b"window.prompt" not in response_data
  assert b"toggleModelMenu" in response_data
  assert b"toggleProviderMenu" in response_data
  assert b"handlePickerMenuKeydown" in response_data
  assert b"selectedModel" in response_data
  assert b"Recommended remediation" not in response_data
  assert b"Generate fix" in response_data
  assert b"Interpretations disagree." in response_data
  assert b"findingEvidence" in response_data
  assert b"findingOrigin" in response_data
  assert b"/api/v1/proposed-fixes" in response_data
  assert b"proposalDiff" in response_data
  assert b"witnessDescription" not in response_data
  assert b"Checks passed" in response_data
  assert b"checked by Unmask against supplied cases" in response_data
  assert b"Validated fix" not in response_data


def test_styles_include_custom_volume_control_and_three_column_shell(
  application: Flask,
) -> None:
  response = application.test_client().get("/assets/style.css")
  response_data: bytes = _response_data(response)

  assert response.status_code == 200
  assert b"grid-template-columns: 285px minmax(560px, 1fr) 310px" in response_data
  assert b'input[type="range"]::-webkit-slider-thumb' in response_data
  assert b"var(--fill" in response_data
  assert b"--inspector-row-height" in response_data
  assert b"--inspector-menu-shadow" in response_data
  assert (
    b"radial-gradient(circle, var(--inspector-dot) 0 1.5px, transparent 2px)"
    in response_data
  )
  assert b"10% 100% repeat-x" in response_data
  assert b"linear-gradient(#aaa8a2" not in response_data
  assert b".picker-menu" in response_data
  assert b".picker-option" in response_data
  assert b".model-option" in response_data
  assert b"white-space: pre" in response_data
  assert b"word-break: normal" in response_data
  assert b".attachment-action" in response_data
  assert b".finding-footer" in response_data
  assert b".generate-fix-action" in response_data
  assert b".finding-origin" in response_data
  assert b"grid-template-columns: minmax(0, 1fr)" in response_data
  assert b".finding-card > * { min-width: 0; }" in response_data


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
  specification = (_EXAMPLES / "regexes.yaml").read_text(encoding="utf-8")
  violations = (_EXAMPLES / "violations.json").read_text(encoding="utf-8")

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
