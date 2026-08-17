"""Command-line entry point for the unmask neuro-symbolic guardrail bug finder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyzer import SymbolicAnalyzer
from .backend import GeminiBackend
from .config import BugFinderConfig
from .diagnosis import DiagnosisConfig, DiagnosisReport, RegexDiagnoser
from .extractor import SemanticExtractor
from .models import Finding, Guardrail, SymbolicModel
from .visualization import DiagnosisVisualizer, write_visualization


def _write_json(path: Path, data: object) -> None:
  _ = path.write_text(json.dumps(data, indent=2), encoding="utf-8")
  print(f"wrote {path}")


def _run_extract(config_path: Path) -> int:
  config: BugFinderConfig = BugFinderConfig.from_yaml(config_path)
  guardrail: Guardrail = config.load_guardrail()
  taxonomy: object | None = config.load_taxonomy()

  backend: GeminiBackend = GeminiBackend(config.target)
  model: SymbolicModel = SemanticExtractor(backend).extract(guardrail, taxonomy)
  findings: list[Finding] = SymbolicAnalyzer(model, guardrail).analyze()

  config.output.directory.mkdir(parents=True, exist_ok=True)
  symbolic_model_path: Path = (
    config.output.directory / config.output.symbolic_model_file
  )
  findings_path: Path = config.output.directory / config.output.findings_file
  _write_json(symbolic_model_path, model.to_dict())
  _write_json(findings_path, [finding.to_dict() for finding in findings])

  print(f"{len(findings)} finding(s)")
  return 0


def _run_diagnose(config_path: Path, *, visualize: bool) -> int:
  config: DiagnosisConfig = DiagnosisConfig.from_yaml(config_path)
  report: DiagnosisReport = RegexDiagnoser().diagnose(config)
  report_dict: dict[str, object] = report.to_dict()

  config.output_path.parent.mkdir(parents=True, exist_ok=True)
  _write_json(config.output_path, report_dict)

  if visualize:
    html: str = DiagnosisVisualizer().render(report_dict)
    visualization_path: Path = config.output_path.with_suffix(".html")
    _ = write_visualization(visualization_path, html)
    print(f"wrote {visualization_path}")

  print(f"{len(report.findings)} finding(s)")
  return 0


def _build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    prog="unmask", description="Neuro-symbolic AI specification bug detection."
  )
  subparsers = parser.add_subparsers(dest="command", required=True)

  extract_parser = subparsers.add_parser(
    "extract",
    help="extract a symbolic model from a natural-language guardrail and analyze it",
  )
  extract_parser.add_argument(
    "config", type=Path, help="path to a bug finder config YAML file"
  )

  diagnose_parser = subparsers.add_parser(
    "diagnose", help="diagnose a regex guardrail against a conversation incident"
  )
  diagnose_parser.add_argument(
    "config", type=Path, help="path to a diagnosis config YAML file"
  )
  diagnose_parser.add_argument(
    "--visualize", action="store_true", help="also render an offline HTML explanation"
  )

  return parser


def main(argv: list[str] | None = None) -> int:
  """Parse CLI arguments and dispatch to the extract or diagnose pipeline.

  Raises:
    SystemExit: if argument parsing fails or `--help` is requested, via argparse.
  """
  args: argparse.Namespace = _build_parser().parse_args(argv)

  try:
    if args.command == "extract":
      return _run_extract(args.config)
    return _run_diagnose(args.config, visualize=args.visualize)
  except (ValueError, TypeError, RuntimeError, OSError) as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  sys.exit(main())
