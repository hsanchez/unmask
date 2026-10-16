import json
import webbrowser
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import unmask.cli as cli_module
from tests.helpers import REPLAY_FIXTURES, load_json_object, write_yaml
from unmask.backend import JudgeResponse
from unmask.cli import main
from unmask.config import TargetConfig
from unmask.models import SymbolicModel
from unmask.service import BackendFactory, ScanResult, bugs


def _write_guardrail(tmp_path: Path) -> Path:
  return write_yaml(
    tmp_path / "guardrail.yaml",
    {
      "name": "no-medical-advice",
      "system_prompt": "You are a helpful assistant.",
      "rules": ["never give medical advice"],
    },
  )


def _write_regex_manifest(tmp_path: Path, *, examples: bool = False) -> Path:
  data: dict[str, object] = {
    "schema_version": 1,
    "kind": "regex",
    "name": "investment-claims",
    "engine": "python_re",
    "scope": "agent_output",
    "rules": [
      {
        "id": "investment",
        "action": "block",
        "pattern": r"\binvest\b",
        "flags": ["IGNORECASE"],
      }
    ],
  }
  if examples:
    data["examples"] = [
      {
        "content": "Anthropic will invest in research.",
        "expected_action": "allow",
        "reason": "This reports a third-party action.",
      }
    ]
  return write_yaml(tmp_path / "regexes.yaml", data)


def _write_violations(tmp_path: Path, *, count: int = 1) -> Path:
  violations: list[dict[str, object]] = [
    {
      "context": f"Company {index} will invest in research.",
      "location": 1000 + index,
      "value": "invest",
      "expected_outcome": "allow",
      "expected_reason": "This reports a third-party action.",
    }
    for index in range(1, count + 1)
  ]
  path: Path = tmp_path / "violations.json"
  _ = path.write_text(json.dumps({"violations": violations}), encoding="utf-8")
  return path


class FakeBackend:
  def __init__(self, config: TargetConfig) -> None:
    self.config: TargetConfig = config
    self.prompts: list[str] = []

  def judge(self, prompt: str, *, max_tokens: int = 8192) -> JudgeResponse:
    del max_tokens
    self.prompts.append(prompt)
    return JudgeResponse(
      json.dumps(
        {
          "predicates": {"is_educational": "request seeks neutral education"},
          "safe_condition": [[{"predicate": "is_educational", "value": True}]],
          "rules": [
            {
              "rule_id": "rule:1",
              "decision": "block",
              "condition": [[{"predicate": "is_educational", "value": False}]],
            }
          ],
        }
      )
    )


class TestMainArgumentParsing:
  def test_requires_a_subcommand(self) -> None:
    with pytest.raises(SystemExit):
      _ = main([])

  def test_rejects_unknown_subcommand(self) -> None:
    with pytest.raises(SystemExit):
      _ = main(["bogus"])

  def test_rejects_negative_thinking_budget(self, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
      _ = main(["scan", str(_write_guardrail(tmp_path)), "--thinking-budget", "-1"])

  def test_rejects_unknown_provider(self, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
      _ = main(["scan", str(_write_guardrail(tmp_path)), "--provider", "bogus"])

  def test_extract_is_not_a_public_command(self) -> None:
    with pytest.raises(SystemExit):
      _ = main(["extract"])

  def test_rejects_non_integer_thinking_budget(
    self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
  ) -> None:
    with pytest.raises(SystemExit):
      _ = main(["scan", str(_write_guardrail(tmp_path)), "--thinking-budget", "many"])

    assert "must be an integer" in capsys.readouterr().err

  @pytest.mark.parametrize("port", ["-1", "65536"])
  def test_rejects_invalid_serve_port(self, port: str) -> None:
    with pytest.raises(SystemExit):
      _ = main(["serve", "--port", port])

  def test_serve_dispatches_local_studio(self, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, bool]] = []

    def run_server(*, port: int, open_browser: bool) -> int:
      calls.append((port, open_browser))
      return 0

    monkeypatch.setattr(cli_module, "run_local_server", run_server)

    assert main(["serve", "--port", "0", "--no-open"]) == 0
    assert calls == [(0, False)]


class TestMainScan:
  def test_scans_natural_language_guardrail_and_writes_artifacts(
    self,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
  ) -> None:
    backend = FakeBackend(
      TargetConfig(
        project="unused",
        location="global",
        judge_model="unused",
        thinking_budget=0,
      )
    )

    def backend_factory(config: TargetConfig) -> FakeBackend:
      del config
      return backend

    monkeypatch.setattr(cli_module, "create_backend", backend_factory)
    taxonomy_path = write_yaml(
      tmp_path / "taxonomy.yaml",
      {"intent_classes": {"Allowed": {"description": "safe requests"}}},
    )
    output_directory = tmp_path / "scan-output"

    exit_code = main(
      [
        "scan",
        str(_write_guardrail(tmp_path)),
        "--taxonomy",
        str(taxonomy_path),
        "--output",
        str(output_directory),
        "--project",
        "test-project",
        "--location",
        "us-central1",
        "--model",
        "test-model",
        "--thinking-budget",
        "64",
      ]
    )

    assert exit_code == 0
    symbolic_model_path = output_directory / "symbolic_model.json"
    findings_path = output_directory / "findings.json"
    assert symbolic_model_path.exists()
    assert findings_path.exists()
    model = SymbolicModel.from_dict(
      cast("object", json.loads(symbolic_model_path.read_text()))
    )
    assert model.rules[0].rule_id == "rule:1"
    assert "intent_classes" in backend.prompts[0]
    assert "natural-language scan" in capsys.readouterr().out

  def test_uses_environment_and_default_model_settings(
    self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    captured: list[TargetConfig] = []

    def backend_factory(config: TargetConfig) -> FakeBackend:
      captured.append(config)
      return FakeBackend(config)

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "environment-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west1")
    monkeypatch.setattr(cli_module, "create_backend", backend_factory)

    exit_code = main(
      [
        "scan",
        str(_write_guardrail(tmp_path)),
        "--output",
        str(tmp_path / "output"),
      ]
    )

    assert exit_code == 0
    assert captured == [
      TargetConfig(
        project="environment-project",
        location="europe-west1",
        judge_model="gemini-2.5-flash",
        thinking_budget=128,
      )
    ]

  def test_selects_provider_and_its_default_model(
    self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    captured: list[TargetConfig] = []

    def backend_factory(config: TargetConfig) -> FakeBackend:
      captured.append(config)
      return FakeBackend(config)

    monkeypatch.setattr(cli_module, "create_backend", backend_factory)

    exit_code = main(
      [
        "scan",
        str(_write_guardrail(tmp_path)),
        "--output",
        str(tmp_path / "output"),
        "--provider",
        "openai",
      ]
    )

    assert exit_code == 0
    assert captured[0].provider == "openai"
    assert captured[0].judge_model == "gpt-4o"

  def test_scans_regex_manifest(self, tmp_path: Path) -> None:
    output_directory = tmp_path / "scan-output"

    exit_code = main(
      [
        "scan",
        str(_write_regex_manifest(tmp_path, examples=True)),
        "--output",
        str(output_directory),
      ]
    )

    assert exit_code == 0
    scan = cast(
      "dict[str, object]",
      json.loads((output_directory / "scan.json").read_text()),
    )
    summary = cast("dict[str, object]", scan["summary"])
    assert summary["examples"] == 1
    assert summary["findings"] == 1

  def test_regex_scan_without_examples_preserves_insufficient_evidence(
    self, tmp_path: Path
  ) -> None:
    output_directory = tmp_path / "scan-output"

    exit_code = main(
      [
        "scan",
        str(_write_regex_manifest(tmp_path)),
        "--output",
        str(output_directory),
      ]
    )

    assert exit_code == 0
    scan = cast(
      "dict[str, object]",
      json.loads((output_directory / "scan.json").read_text()),
    )
    summary = cast("dict[str, object]", scan["summary"])
    assert summary["status"] == "insufficient_evidence"

  def test_regex_scan_uses_default_output_directory(
    self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = main(["scan", str(_write_regex_manifest(tmp_path))])

    assert exit_code == 0
    assert (tmp_path / "output/scan/scan.json").exists()

  def test_reports_unknown_specification_kind(
    self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
  ) -> None:
    path = write_yaml(tmp_path / "unknown.yaml", {"kind": "rubric"})

    exit_code = main(["scan", str(path), "--output", str(tmp_path / "output")])

    assert exit_code == 1
    assert "unsupported specification kind" in capsys.readouterr().err


class TestMainDiagnose:
  def test_diagnoses_multiple_incidents_and_writes_collection(
    self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
  ) -> None:
    output_directory = tmp_path / "diagnose-output"

    exit_code = main(
      [
        "diagnose",
        str(_write_regex_manifest(tmp_path)),
        str(_write_violations(tmp_path, count=2)),
        "--output",
        str(output_directory),
      ]
    )

    assert exit_code == 0
    assert (output_directory / "index.html").exists()
    assert (output_directory / "incidents/0001/report.json").exists()
    assert (output_directory / "incidents/0001/report.html").exists()
    assert (output_directory / "incidents/0002/report.json").exists()
    index = (output_directory / "index.html").read_text(encoding="utf-8")
    assert 'href="incidents/0001/report.html"' in index
    assert "diagnosed 2 incident(s)" in capsys.readouterr().out

  def test_open_launches_collection_index(
    self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    opened: list[str] = []

    def open_browser(url: str) -> bool:
      opened.append(url)
      return True

    monkeypatch.setattr(webbrowser, "open", open_browser)
    output_directory = tmp_path / "diagnose-output"

    exit_code = main(
      [
        "diagnose",
        str(_write_regex_manifest(tmp_path)),
        str(_write_violations(tmp_path)),
        "--output",
        str(output_directory),
        "--open",
      ]
    )

    assert exit_code == 0
    assert opened == [(output_directory / "index.html").resolve().as_uri()]

  def test_browser_decline_is_a_non_fatal_warning(
    self,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
  ) -> None:
    def decline_browser(url: str) -> bool:
      del url
      return False

    monkeypatch.setattr(webbrowser, "open", decline_browser)

    exit_code = main(
      [
        "diagnose",
        str(_write_regex_manifest(tmp_path)),
        str(_write_violations(tmp_path)),
        "--output",
        str(tmp_path / "output"),
        "--open",
      ]
    )

    assert exit_code == 0
    assert "warning: could not open" in capsys.readouterr().err

  def test_browser_error_is_a_non_fatal_warning(
    self,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
  ) -> None:
    def broken_browser(url: str) -> bool:
      del url
      raise webbrowser.Error("no runnable browser")

    monkeypatch.setattr(webbrowser, "open", broken_browser)

    exit_code = main(
      [
        "diagnose",
        str(_write_regex_manifest(tmp_path)),
        str(_write_violations(tmp_path)),
        "--output",
        str(tmp_path / "output"),
        "--open",
      ]
    )

    assert exit_code == 0
    assert "no runnable browser" in capsys.readouterr().err

  def test_reports_invalid_violation_location(
    self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
  ) -> None:
    violations_path = tmp_path / "violations.json"
    _ = violations_path.write_text(
      json.dumps(
        {
          "violations": [
            {
              "context": "Company will invest.",
              "location": "1098",
              "value": "invest",
            }
          ]
        }
      ),
      encoding="utf-8",
    )

    exit_code = main(
      [
        "diagnose",
        str(_write_regex_manifest(tmp_path)),
        str(violations_path),
        "--output",
        str(tmp_path / "output"),
      ]
    )

    assert exit_code == 1
    assert "non-negative integer" in capsys.readouterr().err

  def test_generation_failure_does_not_open_browser(
    self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    opened: list[str] = []

    def open_browser(url: str) -> bool:
      opened.append(url)
      return True

    monkeypatch.setattr(webbrowser, "open", open_browser)
    violations_path = tmp_path / "violations.json"
    _ = violations_path.write_text(
      json.dumps(
        {
          "violations": [
            {"context": "Company will invest.", "location": -1, "value": "invest"}
          ]
        }
      ),
      encoding="utf-8",
    )

    exit_code = main(
      [
        "diagnose",
        str(_write_regex_manifest(tmp_path)),
        str(violations_path),
        "--output",
        str(tmp_path / "output"),
        "--open",
      ]
    )

    assert exit_code == 1
    assert opened == []


class TestMainAuditJudge:
  def test_rejects_invalid_json_with_an_error_exit(
    self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
  ) -> None:
    case = tmp_path / "bad.json"
    _ = case.write_text('{"bad":', encoding="utf-8")

    assert main(["audit-judge", str(case)]) == 1
    assert "invalid JSON" in capsys.readouterr().err

  def test_prints_the_report_when_no_output_file_is_given(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    case = REPLAY_FIXTURES / "A_correct_procedurally_adherent.json"

    assert main(["audit-judge", str(case)]) == 0

    report = load_json_object(capsys.readouterr().out)
    assert report["status"] == "completed"


class TestMainScanInvariants:
  @pytest.mark.parametrize(
    "missing,message",
    [
      ("regex_report", "regex scan did not produce a regex report"),
      ("symbolic_model", "natural-language scan did not produce a symbolic model"),
    ],
  )
  def test_incomplete_scan_results_are_reported_as_errors(
    self,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    missing: str,
    message: str,
  ) -> None:
    specification = (
      _write_regex_manifest(tmp_path)
      if missing == "regex_report"
      else _write_guardrail(tmp_path)
    )

    def incomplete(
      text: str,
      *,
      specification_name: str,
      taxonomy_text: str | None,
      model: TargetConfig,
      backend_factory: BackendFactory,
    ) -> ScanResult:
      result = bugs(
        text,
        specification_name=specification_name,
        taxonomy_text=taxonomy_text,
        model=model,
        backend_factory=backend_factory,
      )
      return replace(result, **{missing: None})

    def fake_backend(config: TargetConfig) -> FakeBackend:
      return FakeBackend(config)

    monkeypatch.setattr(cli_module, "bugs", incomplete)
    monkeypatch.setattr(cli_module, "create_backend", fake_backend)

    exit_code = main(["scan", str(specification), "--output", str(tmp_path / "out")])

    assert exit_code == 1
    assert message in capsys.readouterr().err
