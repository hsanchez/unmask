"""Command-line entry point for the unmask neuro-symbolic guardrail bug finder."""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml
from dotenv import dotenv_values, find_dotenv

from .backend import create_backend
from .config import TargetConfig
from .diagnosis import (
  DiagnosisConfig,
  DiagnosisReport,
  RegexDiagnoser,
  RegexManifest,
)
from .importer import ImportedViolation, load_violations
from .service import (
  DEFAULT_MODELS,
  ScanResult,
  diagnosis_status,
  incident_from_violation,
  scan_specification,
)
from .visualization import (
  CollectionResult,
  DiagnosisVisualizer,
  render_collection_index,
  write_visualization,
)
from .webapp import DEFAULT_PORT, run_local_server

_ALLOWED_ENV_VARS: frozenset[str] = frozenset(
  {
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_GENAI_USE_ENTERPRISE",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
  }
)


@dataclass(frozen=True)
class ScanArguments:
  specification: Path
  taxonomy: Path | None
  output: Path
  provider: str
  project: str | None
  location: str
  model: str | None
  thinking_budget: int


@dataclass(frozen=True)
class DiagnoseArguments:
  regexes: Path
  violations: Path
  output: Path
  open_index: bool


@dataclass(frozen=True)
class ServeArguments:
  port: int
  open_browser: bool


type CommandArguments = ScanArguments | DiagnoseArguments | ServeArguments


def _write_json(path: Path, data: object) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  _ = path.write_text(json.dumps(data, indent=2), encoding="utf-8")
  print(f"wrote {path}")


def _add_env_file_argument(parser: argparse.ArgumentParser) -> None:
  _ = parser.add_argument(
    "--env-file",
    type=Path,
    default=None,
    help="load environment variables from this file instead of "
    + "auto-discovering .env from the current directory",
  )


def _non_negative_integer(value: str) -> int:
  try:
    parsed: int = int(value)
  except ValueError as exc:
    raise argparse.ArgumentTypeError("must be an integer") from exc
  if parsed < 0:
    raise argparse.ArgumentTypeError("must be non-negative")
  return parsed


def _port_number(value: str) -> int:
  port: int = _non_negative_integer(value)
  if port > 65_535:
    raise argparse.ArgumentTypeError("must be no greater than 65535")
  return port


def _run_scan(
  specification_path: Path,
  *,
  taxonomy_path: Path | None,
  output_directory: Path,
  target: TargetConfig,
) -> int:
  result: ScanResult = scan_specification(
    specification_path.read_text(encoding="utf-8"),
    specification_name=specification_path.name,
    taxonomy_text=(
      taxonomy_path.read_text(encoding="utf-8") if taxonomy_path is not None else None
    ),
    target=target,
    backend_factory=create_backend,
  )
  if result.specification_kind == "regex":
    if result.regex_report is None:
      raise RuntimeError("regex scan did not produce a regex report")
    report_data: dict[str, object] = result.regex_report.to_dict()
    _write_json(output_directory / "scan.json", report_data)
    summary: dict[str, object] = cast("dict[str, object]", report_data["summary"])
    print(
      f"regex scan: {summary['findings']} finding(s), "
      + f"{summary['examples']} example(s), status={summary['status']}"
    )
    return 0

  if result.symbolic_model is None:
    raise RuntimeError("natural-language scan did not produce a symbolic model")
  _write_json(output_directory / "symbolic_model.json", result.symbolic_model.to_dict())
  _write_json(
    output_directory / "findings.json",
    [
      {
        "id": finding.id,
        "type": finding.bug_type,
        "severity": finding.severity,
        "ruleIds": list(finding.rule_ids),
        "summary": finding.summary,
        "rationale": finding.rationale,
        "witness": dict(sorted(finding.witness.items())),
        "metadata": finding.metadata,
      }
      for finding in result.findings
    ],
  )
  print(f"natural-language scan: {len(result.findings)} finding(s)")
  return 0


def _diagnosis_config(
  manifest_path: Path,
  manifest: RegexManifest,
  violation: ImportedViolation,
  *,
  index: int,
  output_directory: Path,
) -> DiagnosisConfig:
  return DiagnosisConfig(
    source=manifest_path.resolve(),
    name=f"{manifest.name} incident {index}",
    incident=incident_from_violation(violation),
    rules=manifest.rules,
    output_path=output_directory / "incidents" / f"{index:04d}" / "report.json",
    match_timeout_seconds=manifest.match_timeout_seconds,
  )


def _write_diagnosis_artifacts(
  report: DiagnosisReport,
  config: DiagnosisConfig,
  *,
  index: int,
  output_directory: Path,
) -> CollectionResult:
  report_data: dict[str, object] = report.to_dict()
  _write_json(config.output_path, report_data)
  visualization_path: Path = config.output_path.with_suffix(".html")
  html: str = DiagnosisVisualizer().render(report_data)
  _ = write_visualization(visualization_path, html)
  print(f"wrote {visualization_path}")

  relative_report: Path = config.output_path.relative_to(output_directory)
  relative_visualization: Path = visualization_path.relative_to(output_directory)
  source_location: int = cast("int", report.incident.source_location)
  return CollectionResult(
    incident=index,
    sourceLocation=source_location,
    value=report.incident.value,
    findings=len(report.findings),
    status=diagnosis_status(report),
    expectedOutcomeSource=report.incident.expected_outcome_source,
    expectedReason=report.incident.expected_reason,
    config=str(config.source),
    report=relative_report.as_posix(),
    visualization=relative_visualization.as_posix(),
  )


def _open_index(index_path: Path) -> None:
  try:
    opened: bool = webbrowser.open(index_path.resolve().as_uri())
  except (OSError, webbrowser.Error) as exc:
    print(f"warning: could not open {index_path}: {exc}", file=sys.stderr)
    return
  if not opened:
    print(f"warning: could not open {index_path}", file=sys.stderr)


def _run_diagnose(
  manifest_path: Path,
  violations_path: Path,
  *,
  output_directory: Path,
  open_index: bool,
) -> int:
  manifest: RegexManifest = RegexManifest.from_yaml(manifest_path)
  violations: tuple[ImportedViolation, ...] = load_violations(violations_path)
  configs: tuple[DiagnosisConfig, ...] = tuple(
    _diagnosis_config(
      manifest_path,
      manifest,
      violation,
      index=index,
      output_directory=output_directory,
    )
    for index, violation in enumerate(violations, start=1)
  )
  reports: tuple[DiagnosisReport, ...] = RegexDiagnoser().diagnose_many(configs)
  results: list[CollectionResult] = [
    _write_diagnosis_artifacts(
      report,
      config,
      index=index,
      output_directory=output_directory,
    )
    for index, (report, config) in enumerate(
      zip(reports, configs, strict=True),
      start=1,
    )
  ]

  index_path: Path = output_directory / "index.html"
  _ = write_visualization(index_path, render_collection_index(results))
  print(f"wrote {index_path}")
  finding_count: int = sum(len(report.findings) for report in reports)
  skipped_count: int = sum(len(report.skipped_analyses) for report in reports)
  limited_count: int = sum(bool(report.skipped_analyses) for report in reports)
  print(
    f"diagnosed {len(reports)} incident(s): {finding_count} finding(s), "
    + f"{skipped_count} skipped analysis(es), {limited_count} limited incident(s)"
  )
  if open_index:
    _open_index(index_path)
  return 0


def _build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    prog="unmask", description="Neuro-symbolic AI specification bug detection."
  )
  subparsers = parser.add_subparsers(dest="command", required=True)

  scan_parser = subparsers.add_parser(
    "scan",
    help="scan a natural-language or regex guardrail specification for defects",
  )
  _ = scan_parser.add_argument("specification", type=Path, help="guardrail YAML file")
  _ = scan_parser.add_argument(
    "--taxonomy", type=Path, help="optional taxonomy YAML file"
  )
  _ = scan_parser.add_argument(
    "--output",
    type=Path,
    default=Path("output/scan"),
    help="artifact directory (default: output/scan)",
  )
  _ = scan_parser.add_argument(
    "--provider",
    choices=sorted(DEFAULT_MODELS),
    default="gemini",
    help="judge backend for natural-language scans (default: gemini)",
  )
  _ = scan_parser.add_argument(
    "--project",
    default=os.getenv("GOOGLE_CLOUD_PROJECT"),
    help="Google Cloud project; Vertex AI mode for the gemini provider "
    + "(default: GOOGLE_CLOUD_PROJECT, or the Gemini Developer API if unset)",
  )
  _ = scan_parser.add_argument(
    "--location",
    default=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
    help="Vertex AI location (default: GOOGLE_CLOUD_LOCATION or global)",
  )
  _ = scan_parser.add_argument(
    "--model",
    default=None,
    help="judge model (default: provider-specific)",
  )
  _ = scan_parser.add_argument(
    "--thinking-budget",
    type=_non_negative_integer,
    default=128,
    help="judge thinking budget (default: 128)",
  )
  _add_env_file_argument(scan_parser)

  diagnose_parser = subparsers.add_parser(
    "diagnose", help="diagnose reported violations against a regex guardrail"
  )
  _ = diagnose_parser.add_argument(
    "regexes", type=Path, help="regex manifest YAML file"
  )
  _ = diagnose_parser.add_argument("violations", type=Path, help="violations JSON file")
  _ = diagnose_parser.add_argument(
    "--output",
    type=Path,
    default=Path("output/diagnose"),
    help="artifact directory (default: output/diagnose)",
  )
  _ = diagnose_parser.add_argument(
    "--open",
    action="store_true",
    dest="open_index",
    help="open the generated collection index in a browser",
  )
  _add_env_file_argument(diagnose_parser)

  serve_parser = subparsers.add_parser(
    "serve", help="start the local browser analysis studio"
  )
  _ = serve_parser.add_argument(
    "--port",
    type=_port_number,
    default=DEFAULT_PORT,
    help=f"loopback port, or 0 for an available port (default: {DEFAULT_PORT})",
  )
  _ = serve_parser.add_argument(
    "--no-open",
    action="store_false",
    dest="open_browser",
    help="do not open the default browser",
  )
  _add_env_file_argument(serve_parser)

  return parser


def _extract_env_file(argv: list[str] | None) -> Path | None:
  pre_parser = argparse.ArgumentParser(add_help=False)
  _ = pre_parser.add_argument("--env-file", type=Path, default=None)
  known, _ = pre_parser.parse_known_args(argv)
  return cast("Path | None", known.env_file)


def _load_environment(env_file: Path | None) -> None:
  """Load only the credential/project variables `unmask` recognizes from a
  `.env` file, ignoring every other entry.

  A specification file (e.g. a guardrail YAML) is often processed from a
  cloned or downloaded repository. Loading a full `.env` from that checkout
  would let it inject unrelated settings, such as an SDK base-URL override,
  into the process. Restricting the load to `_ALLOWED_ENV_VARS` closes that
  path while preserving `.env` convenience for the variables unmask actually
  reads. Variables already set in the environment take precedence.
  """
  resolved_path: str = (
    str(env_file) if env_file is not None else find_dotenv(usecwd=True)
  )
  if not resolved_path:
    return
  values: dict[str, str | None] = dotenv_values(resolved_path)
  for name in _ALLOWED_ENV_VARS:
    value: str | None = values.get(name)
    if value is not None and name not in os.environ:
      os.environ[name] = value


def _parse_arguments(argv: list[str] | None) -> CommandArguments:
  namespace: argparse.Namespace = _build_parser().parse_args(argv)
  values: dict[str, object] = cast("dict[str, object]", vars(namespace))
  if values["command"] == "scan":
    return ScanArguments(
      specification=cast("Path", values["specification"]),
      taxonomy=cast("Path | None", values["taxonomy"]),
      output=cast("Path", values["output"]),
      provider=cast("str", values["provider"]),
      project=cast("str | None", values["project"]),
      location=cast("str", values["location"]),
      model=cast("str | None", values["model"]),
      thinking_budget=cast("int", values["thinking_budget"]),
    )
  if values["command"] == "diagnose":
    return DiagnoseArguments(
      regexes=cast("Path", values["regexes"]),
      violations=cast("Path", values["violations"]),
      output=cast("Path", values["output"]),
      open_index=cast("bool", values["open_index"]),
    )
  return ServeArguments(
    port=cast("int", values["port"]),
    open_browser=cast("bool", values["open_browser"]),
  )


def main(argv: list[str] | None = None) -> int:
  """Parse CLI arguments and dispatch to a scan, diagnosis, or local studio.

  Raises:
    SystemExit: if argument parsing fails or `--help` is requested, via argparse.
  """
  _load_environment(_extract_env_file(argv))
  args: CommandArguments = _parse_arguments(argv)

  try:
    if isinstance(args, ScanArguments):
      return _run_scan(
        args.specification,
        taxonomy_path=args.taxonomy,
        output_directory=args.output,
        target=TargetConfig(
          project=args.project,
          location=args.location,
          judge_model=args.model or DEFAULT_MODELS[args.provider],
          thinking_budget=args.thinking_budget,
          provider=args.provider,
        ),
      )
    if isinstance(args, DiagnoseArguments):
      return _run_diagnose(
        args.regexes,
        args.violations,
        output_directory=args.output,
        open_index=args.open_index,
      )
    return run_local_server(port=args.port, open_browser=args.open_browser)
  except (OSError, RuntimeError, TypeError, ValueError, yaml.YAMLError) as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  sys.exit(main())
