"""Local Flask application for the Unmask analysis studio."""

from __future__ import annotations

import logging
import os
import sys
import time
import webbrowser
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from threading import BoundedSemaphore
from typing import Protocol, cast

import yaml
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType
from werkzeug.serving import make_server

from .config import TargetConfig
from .judge_audit.service import AUDITOR_VERSION, audit_judge_case, validate_audit_case
from .repair import PROPOSED_FIX_SCHEMA_VERSION, RepairGenerationError
from .service import (
  DEFAULT_MODELS,
  PROVIDER_MODELS,
  ScanResult,
  diagnose_specification,
  propose_fix,
  scan_specification,
  validate_specification,
)

MAX_REQUEST_BYTES = 1_000_000
DEFAULT_PORT = 8765
logger = logging.getLogger(__name__)


def _application_version() -> str:
  try:
    return version("unmask")
  except PackageNotFoundError:
    return "development"


APPLICATION_VERSION = _application_version()


class _LocalServer(Protocol):
  server_port: int

  def serve_forever(self) -> None: ...

  def shutdown(self) -> None: ...

  def server_close(self) -> None: ...


def _error(message: str, *, code: str, status: int) -> tuple[Response, int]:
  return jsonify({"error": {"code": code, "message": message}}), status


def _cross_origin_request() -> bool:
  origin: str | None = request.headers.get("Origin")
  return (
    origin is not None and origin != request.host_url.rstrip("/")
  ) or request.headers.get("Sec-Fetch-Site") == "cross-site"


def _json_object() -> dict[str, object]:
  try:
    raw = cast("object", request.get_json(force=False, silent=False))
  except (BadRequest, UnsupportedMediaType) as exc:
    raise ValueError("request body must contain valid JSON") from exc
  if not isinstance(raw, dict):
    raise TypeError("request body must be a JSON object")
  return cast("dict[str, object]", raw)


def _only_fields(data: dict[str, object], allowed: frozenset[str]) -> None:
  unknown: list[str] = sorted(set(data) - allowed)
  if unknown:
    raise ValueError(f"unknown request fields: {', '.join(unknown)}")


def _document(raw: object, *, field: str) -> tuple[str, str]:
  if not isinstance(raw, dict):
    raise TypeError(f"{field} must be an object")
  data: dict[str, object] = cast("dict[str, object]", raw)
  _only_fields(data, frozenset({"name", "text"}))
  name: object = data.get("name", f"{field}.yaml")
  text: object = data.get("text")
  if not isinstance(name, str) or not name.strip():
    raise ValueError(f"{field}.name must be a non-empty string")
  if not isinstance(text, str) or not text.strip():
    raise ValueError(f"{field}.text must be a non-empty string")
  return name.strip(), text


def _optional_document(raw: object, *, field: str) -> tuple[str, str] | None:
  if raw is None:
    return None
  return _document(raw, field=field)


def _integer(raw: object, *, field: str, default: int) -> int:
  if raw is None:
    return default
  if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
    raise ValueError(f"{field} must be a non-negative integer")
  return raw


def _provider_configured(provider: str) -> bool:
  if provider == "gemini":
    return any(
      os.getenv(name)
      for name in ("GOOGLE_CLOUD_PROJECT", "GOOGLE_API_KEY", "GEMINI_API_KEY")
    )
  env_var: str = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
  }[provider]
  return bool(os.getenv(env_var))


def _target_config(data: dict[str, object]) -> TargetConfig:
  provider: object = data.get("provider", "gemini")
  if not isinstance(provider, str) or provider not in DEFAULT_MODELS:
    raise ValueError(f"unsupported provider: {provider!r}")
  model: object = data.get("model", DEFAULT_MODELS[provider])
  if not isinstance(model, str) or not model.strip():
    raise ValueError("model must be a non-empty string")
  return TargetConfig(
    project=os.getenv("GOOGLE_CLOUD_PROJECT"),
    location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
    judge_model=model.strip(),
    thinking_budget=_integer(
      data.get("thinkingBudget"), field="thinkingBudget", default=128
    ),
    provider=provider,
  )


def _packaged_text(name: str) -> str:
  return files("unmask").joinpath("web", "samples", name).read_text(encoding="utf-8")


def _sample(
  title: str,
  filename: str,
  *,
  incidents_name: str | None = None,
) -> dict[str, str]:
  sample: dict[str, str] = {
    "id": filename.removesuffix(".yaml"),
    "title": title,
    "filename": filename,
    "text": _packaged_text(filename),
  }
  if incidents_name is not None:
    sample["incidentsName"] = incidents_name
    sample["incidentsText"] = _packaged_text(incidents_name)
  return sample


def create_app() -> Flask:
  """Create the local analysis-studio application."""
  application = Flask(
    __name__,
    static_folder="web/assets",
    static_url_path="/assets",
    template_folder="web",
  )
  application.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
  application.config["JSON_SORT_KEYS"] = False
  application.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
  application.config["TEMPLATES_AUTO_RELOAD"] = True
  application.config["TRUSTED_HOSTS"] = ["127.0.0.1", "localhost", "[::1]"]
  audit_slots = BoundedSemaphore(value=1)
  application.extensions["unmask_audit_slots"] = audit_slots
  proposed_fix_slots = BoundedSemaphore(value=1)
  application.extensions["unmask_proposed_fix_slots"] = proposed_fix_slots

  def security_headers(response: Response) -> Response:
    response.headers["Content-Security-Policy"] = (
      "default-src 'self'; script-src 'self'; style-src 'self'; "
      "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response

  _ = application.after_request(security_headers)

  def request_too_large(_: RequestEntityTooLarge) -> tuple[Response, int]:
    return _error(
      f"request exceeds the {MAX_REQUEST_BYTES}-byte limit",
      code="request_too_large",
      status=413,
    )

  application.register_error_handler(RequestEntityTooLarge, request_too_large)

  def index() -> str:
    return render_template("index.html")

  application.add_url_rule("/", view_func=index)

  def capabilities() -> Response:
    providers: list[dict[str, object]] = [
      {
        "id": provider,
        "defaultModel": model,
        "models": PROVIDER_MODELS[provider],
        "configured": _provider_configured(provider),
      }
      for provider, model in DEFAULT_MODELS.items()
    ]
    return jsonify(
      {
        "schemaVersion": 1,
        "version": APPLICATION_VERSION,
        "specificationKinds": ["natural_language", "regex"],
        "workflows": ["scan", "diagnose", "audit_judge"],
        "judgeAudits": {
          "schemaVersion": 1,
          "auditorVersion": AUDITOR_VERSION,
          "mode": "replay",
        },
        "diagnosisSpecificationKinds": ["regex"],
        "providers": providers,
        "maxRequestBytes": MAX_REQUEST_BYTES,
        "storage": "browser_local",
        "proposedFixes": {
          "enabled": True,
          "schemaVersion": PROPOSED_FIX_SCHEMA_VERSION,
          "workflows": ["scan", "diagnose"],
          "specificationKinds": ["natural_language", "regex"],
          "applySupported": False,
        },
      }
    )

  application.add_url_rule("/api/v1/capabilities", view_func=capabilities)

  def samples() -> Response:
    return jsonify(
      {
        "samples": [
          _sample("Natural-language guardrail", "guardrail.yaml"),
          _sample(
            "Regex guardrail",
            "regexes.yaml",
            incidents_name="violations.json",
          ),
        ]
      }
    )

  application.add_url_rule("/api/v1/samples", view_func=samples)

  def validate() -> tuple[Response, int] | Response:
    try:
      data: dict[str, object] = _json_object()
      _only_fields(data, frozenset({"specification"}))
      name, text = _document(data.get("specification"), field="specification")
      return jsonify(validate_specification(text, name=name).to_dict())
    except (TypeError, ValueError, yaml.YAMLError) as exc:
      return _error(str(exc), code="invalid_specification", status=400)

  application.add_url_rule("/api/v1/validate", methods=("POST",), view_func=validate)

  def scans() -> tuple[Response, int] | Response:
    try:
      data: dict[str, object] = _json_object()
      _only_fields(
        data,
        frozenset(
          {
            "specification",
            "taxonomy",
            "provider",
            "model",
            "thinkingBudget",
          }
        ),
      )
      specification_name, specification_text = _document(
        data.get("specification"), field="specification"
      )
      taxonomy = _optional_document(data.get("taxonomy"), field="taxonomy")
      target: TargetConfig = _target_config(data)
      result: ScanResult = scan_specification(
        specification_text,
        specification_name=specification_name,
        taxonomy_text=taxonomy[1] if taxonomy is not None else None,
        target=target,
      )
      return jsonify(result.to_dict())
    except (TypeError, ValueError, yaml.YAMLError) as exc:
      return _error(str(exc), code="invalid_scan", status=400)
    except RuntimeError as exc:
      return _error(str(exc), code="scan_failed", status=422)

  application.add_url_rule("/api/v1/scans", methods=("POST",), view_func=scans)

  def diagnoses() -> tuple[Response, int] | Response:
    try:
      data: dict[str, object] = _json_object()
      _only_fields(data, frozenset({"specification", "violations"}))
      specification_name, specification_text = _document(
        data.get("specification"), field="specification"
      )
      _, violations_text = _document(data.get("violations"), field="violations")
      result: ScanResult = diagnose_specification(
        specification_text,
        violations_text,
        specification_name=specification_name,
      )
      return jsonify(result.to_dict())
    except (TypeError, ValueError, yaml.YAMLError) as exc:
      return _error(str(exc), code="invalid_diagnosis", status=400)
    except RuntimeError as exc:
      return _error(str(exc), code="diagnosis_failed", status=422)

  application.add_url_rule("/api/v1/diagnoses", methods=("POST",), view_func=diagnoses)

  def audit_example() -> Response:
    return jsonify(
      {"name": "judge-audit.json", "text": _packaged_text("judge-audit.json")}
    )

  application.add_url_rule(
    "/api/v1/judge-audits/example", methods=("GET",), view_func=audit_example
  )

  def validate_audit() -> tuple[Response, int] | Response:
    if _cross_origin_request():
      return _error(
        "cross-origin requests are not allowed", code="invalid_origin", status=403
      )
    try:
      data = _json_object()
      _only_fields(data, frozenset({"case"}))
      _, text = _document(data.get("case"), field="case")
      return jsonify(validate_audit_case(text))
    except (TypeError, ValueError) as exc:
      return _error(str(exc), code="invalid_audit_case", status=400)

  application.add_url_rule(
    "/api/v1/judge-audits/validate", methods=("POST",), view_func=validate_audit
  )

  def judge_audits() -> tuple[Response, int] | Response:
    if _cross_origin_request():
      return _error(
        "cross-origin requests are not allowed", code="invalid_origin", status=403
      )
    if not audit_slots.acquire(blocking=False):
      return _error(
        "An audit is already running. Try again when it finishes.",
        code="audit_busy",
        status=409,
      )
    try:
      data = _json_object()
      _only_fields(data, frozenset({"case", "contentHash"}))
      _, text = _document(data.get("case"), field="case")
      validated = validate_audit_case(text)
      if data.get("contentHash") != validated["contentHash"]:
        return _error(
          "The audit case changed. Validate it again before running.",
          code="stale_audit_case",
          status=409,
        )
      return jsonify(audit_judge_case(text))
    except (TypeError, ValueError) as exc:
      return _error(str(exc), code="invalid_audit_case", status=400)
    except RuntimeError:
      logger.exception("judge audit failed")
      return _error(
        "The local solver could not complete this audit.",
        code="audit_failed",
        status=422,
      )
    finally:
      audit_slots.release()

  application.add_url_rule(
    "/api/v1/judge-audits", methods=("POST",), view_func=judge_audits
  )

  def proposed_fixes() -> tuple[Response, int] | Response:
    if _cross_origin_request():
      return _error(
        "cross-origin repair requests are not allowed",
        code="invalid_origin",
        status=403,
      )
    if not proposed_fix_slots.acquire(blocking=False):
      return _error(
        "another proposed fix is already being checked",
        code="proposed_fix_busy",
        status=429,
      )
    started: float = time.perf_counter()
    try:
      data: dict[str, object] = _json_object()
      _only_fields(
        data,
        frozenset(
          {
            "specification",
            "taxonomy",
            "violations",
            "workflow",
            "findingId",
            "contentHash",
            "provider",
            "model",
            "thinkingBudget",
          }
        ),
      )
      specification_name, specification_text = _document(
        data.get("specification"), field="specification"
      )
      taxonomy = _optional_document(data.get("taxonomy"), field="taxonomy")
      violations = _optional_document(data.get("violations"), field="violations")
      workflow: object = data.get("workflow")
      if workflow not in ("scan", "diagnose"):
        raise ValueError("workflow must be 'scan' or 'diagnose'")
      finding_id: object = data.get("findingId")
      if not isinstance(finding_id, str) or not finding_id:
        raise ValueError("findingId must be a non-empty string")
      requested_hash: object = data.get("contentHash")
      if not isinstance(requested_hash, str) or not requested_hash:
        raise ValueError("contentHash must be a non-empty string")
      target: TargetConfig = _target_config(data)
      result = propose_fix(
        specification_text,
        specification_name=specification_name,
        taxonomy_text=taxonomy[1] if taxonomy is not None else None,
        violations_text=violations[1] if violations is not None else None,
        workflow=workflow,
        finding_id=finding_id,
        expected_content_hash=requested_hash,
        target=target,
      )
      logger.info(
        "proposed-fix request completed",
        extra={
          "workflow": workflow,
          "finding_id": finding_id,
          "fix_status": result.status.value,
          "unavailable_reasons": [
            reason.value for reason in result.unavailable_reasons
          ],
        },
      )
      return jsonify(result.to_dict())
    except LookupError as exc:
      return _error(str(exc), code="stale_analysis", status=409)
    except RepairGenerationError as exc:
      logger.warning("proposed-fix generation failed", exc_info=True)
      return _error(str(exc), code="proposed_fix_failed", status=422)
    except (TypeError, ValueError, yaml.YAMLError) as exc:
      logger.warning("proposed-fix input or validation failed", exc_info=True)
      return _error(str(exc), code="invalid_proposed_fix", status=400)
    except RuntimeError as exc:
      logger.warning("proposed-fix execution failed", exc_info=True)
      return _error(str(exc), code="proposed_fix_failed", status=422)
    finally:
      proposed_fix_slots.release()
      logger.info(
        "proposed-fix slot released",
        extra={
          "latency_ms": round((time.perf_counter() - started) * 1_000),
        },
      )

  application.add_url_rule(
    "/api/v1/proposed-fixes", methods=("POST",), view_func=proposed_fixes
  )

  return application


def run_local_server(
  *,
  port: int = DEFAULT_PORT,
  open_browser: bool = True,
  browser_opener: Callable[[str], bool] = webbrowser.open,
  server_factory: Callable[..., _LocalServer] = make_server,
) -> int:
  """Run the analysis studio on loopback until interrupted.

  Raises:
    OSError: if the server cannot bind to the requested port.
  """
  application: Flask = create_app()
  server: _LocalServer = server_factory("127.0.0.1", port, application)
  actual_port: int = server.server_port
  url: str = f"http://127.0.0.1:{actual_port}/"
  print(f"Unmask studio: {url}")
  if open_browser:
    try:
      opened: bool = browser_opener(url)
    except (OSError, webbrowser.Error) as exc:
      print(f"warning: could not open {url}: {exc}", file=sys.stderr)
    else:
      if not opened:
        print(f"warning: could not open {url}", file=sys.stderr)
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    pass
  finally:
    server.shutdown()
    server.server_close()
  return 0
