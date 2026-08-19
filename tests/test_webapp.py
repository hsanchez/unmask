from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from flask import Flask

from unmask.webapp import MAX_REQUEST_BYTES, create_app, run_local_server

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def application() -> Flask:
  app: Flask = create_app()
  app.config["TESTING"] = True
  return app


def test_index_serves_studio_with_security_headers(application: Flask) -> None:
  response = application.test_client().get("/")

  assert response.status_code == 200
  assert b"Local analysis studio" in response.data
  assert b"Load a file to scan or diagnose" in response.data
  assert b'id="scan-mode"' in response.data
  assert b'id="diagnose-mode"' in response.data
  assert b'id="analyze-button"' in response.data
  assert response.data.index(b'id="source-name"') < response.data.index(
    b'id="taxonomy-button"'
  )
  assert b'id="regex-model-note"' in response.data
  assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
  assert "no-store" in response.headers["Cache-Control"]


def test_assets_disable_cache_for_local_development(application: Flask) -> None:
  response = application.test_client().get("/assets/app.js")

  assert response.status_code == 200
  assert b"indexedDB" in response.data
  assert b"textContent" in response.data
  assert b"innerHTML" not in response.data
  assert b"renderEmptyWorkspace()" in response.data
  assert b"/api/v1/diagnoses" in response.data
  assert (
    b'elements.providerControl.hidden = isRegex || workflow === "diagnose"'
    in response.data
  )


def test_capabilities_never_expose_credential_values(
  application: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setenv("OPENAI_API_KEY", "secret-value")

  response = application.test_client().get("/api/v1/capabilities")
  payload = cast("dict[str, object]", response.get_json())

  assert response.status_code == 200
  assert "secret-value" not in response.text
  assert payload["storage"] == "browser_local"


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
  assert int(cast("dict[str, object]", payload["summary"])["incidents"]) == 3
  assert payload["findings"]


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
  server_port = 43123

  def __init__(self) -> None:
    self.served = False
    self.shutdown_called = False
    self.closed = False

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
    server_factory=cast("Callable[..., object]", server_factory),
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
    server_factory=cast("Callable[..., object]", server_factory),
  )

  assert exit_code == 0
  assert server.served
  assert "warning: could not open" in capsys.readouterr().err
