"""Local Flask application for the Unmask analysis studio."""

from __future__ import annotations

import os
import sys
import webbrowser
from collections.abc import Callable
from importlib.resources import files
from typing import cast

import yaml
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType
from werkzeug.serving import BaseWSGIServer, make_server

from .config import TargetConfig
from .service import (
  DEFAULT_MODELS,
  ScanResult,
  diagnose_specification,
  scan_specification,
  validate_specification,
)

MAX_REQUEST_BYTES = 1_000_000
DEFAULT_PORT = 8765


def _error(message: str, *, code: str, status: int) -> tuple[Response, int]:
  return jsonify({"error": {"code": code, "message": message}}), status


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
        "configured": _provider_configured(provider),
      }
      for provider, model in DEFAULT_MODELS.items()
    ]
    return jsonify(
      {
        "schemaVersion": 1,
        "specificationKinds": ["natural_language", "regex"],
        "workflows": ["scan", "diagnose"],
        "diagnosisSpecificationKinds": ["regex"],
        "providers": providers,
        "maxRequestBytes": MAX_REQUEST_BYTES,
        "storage": "browser_local",
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
      provider: object = data.get("provider", "gemini")
      if not isinstance(provider, str) or provider not in DEFAULT_MODELS:
        raise ValueError(f"unsupported provider: {provider!r}")
      model: object = data.get("model", DEFAULT_MODELS[provider])
      if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
      result: ScanResult = scan_specification(
        specification_text,
        specification_name=specification_name,
        taxonomy_text=taxonomy[1] if taxonomy is not None else None,
        target=TargetConfig(
          project=os.getenv("GOOGLE_CLOUD_PROJECT"),
          location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
          judge_model=model.strip(),
          thinking_budget=_integer(
            data.get("thinkingBudget"), field="thinkingBudget", default=128
          ),
          provider=provider,
        ),
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

  return application


def run_local_server(
  *,
  port: int = DEFAULT_PORT,
  open_browser: bool = True,
  browser_opener: Callable[[str], bool] = webbrowser.open,
  server_factory: Callable[..., BaseWSGIServer] = make_server,
) -> int:
  """Run the analysis studio on loopback until interrupted.

  Raises:
    OSError: if the server cannot bind to the requested port.
  """
  application: Flask = create_app()
  server: BaseWSGIServer = server_factory("127.0.0.1", port, application)
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
