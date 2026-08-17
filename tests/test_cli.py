import json
from pathlib import Path
from typing import cast

import pytest

import unmask.cli as cli_module
from tests.helpers import write_yaml
from unmask.cli import main
from unmask.config import TargetConfig
from unmask.models import SymbolicModel


def _write_extract_config(tmp_path: Path) -> Path:
  _ = write_yaml(
    tmp_path / "guardrail.yaml",
    {
      "name": "no-medical-advice",
      "system_prompt": "You are a helpful assistant.",
      "rules": ["never give medical advice"],
    },
  )
  return write_yaml(
    tmp_path / "config.yaml",
    {
      "guardrail": {"file": "guardrail.yaml"},
      "target": {"project": "test-project"},
    },
  )


def _write_diagnose_config(tmp_path: Path, *, matching: bool) -> Path:
  return write_yaml(
    tmp_path / "diagnosis.yaml",
    {
      "name": "test-case",
      "conversation": {
        "text": "please hack this",
        "value": "hack" if matching else "please hack this",
        "location": {"start": 7, "end": 11} if matching else {"start": 0, "end": 17},
      },
      "rules": [{"id": "rule:1", "pattern": "hack", "action": "block"}],
      "output": "diagnosis.json",
    },
  )


class FakeBackend:
  def __init__(self, config: TargetConfig) -> None:
    del config

  def judge(self, prompt: str, *, max_tokens: int = 8192) -> str:
    del prompt, max_tokens
    return json.dumps(
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


class TestMainArgumentParsing:
  def test_requires_a_subcommand(self) -> None:
    with pytest.raises(SystemExit):
      _ = main([])

  def test_rejects_unknown_subcommand(self) -> None:
    with pytest.raises(SystemExit):
      _ = main(["bogus"])


class TestMainExtract:
  def test_writes_symbolic_model_and_findings(
    self,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
  ) -> None:
    monkeypatch.setattr(cli_module, "GeminiBackend", FakeBackend)
    config_path = _write_extract_config(tmp_path)

    exit_code = main(["extract", str(config_path)])

    assert exit_code == 0
    symbolic_model_path = tmp_path / "output" / "symbolic_model.json"
    findings_path = tmp_path / "output" / "findings.json"
    assert symbolic_model_path.exists()
    assert findings_path.exists()
    model = SymbolicModel.from_dict(
      cast("object", json.loads(symbolic_model_path.read_text()))
    )
    assert model.rules[0].rule_id == "rule:1"
    assert "wrote" in capsys.readouterr().out

  def test_reports_config_errors_on_stderr(
    self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
  ) -> None:
    config_path = write_yaml(tmp_path / "config.yaml", {})

    exit_code = main(["extract", str(config_path)])

    assert exit_code == 1
    assert "guardrail.file" in capsys.readouterr().err


class TestMainDiagnose:
  def test_writes_report_json(
    self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
  ) -> None:
    config_path = _write_diagnose_config(tmp_path, matching=True)

    exit_code = main(["diagnose", str(config_path)])

    assert exit_code == 0
    report_path = tmp_path / "diagnosis.json"
    assert report_path.exists()
    report = cast("object", json.loads(report_path.read_text()))
    assert isinstance(report, dict)
    assert report["name"] == "test-case"
    assert "finding(s)" in capsys.readouterr().out

  def test_visualize_also_writes_html(self, tmp_path: Path) -> None:
    config_path = _write_diagnose_config(tmp_path, matching=True)

    exit_code = main(["diagnose", str(config_path), "--visualize"])

    assert exit_code == 0
    assert (tmp_path / "diagnosis.html").exists()

  def test_reports_config_errors_on_stderr(
    self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
  ) -> None:
    config_path = write_yaml(
      tmp_path / "diagnosis.yaml", {"name": "test", "conversation": {}, "rules": []}
    )

    exit_code = main(["diagnose", str(config_path)])

    assert exit_code == 1
    assert "non-empty list" in capsys.readouterr().err
