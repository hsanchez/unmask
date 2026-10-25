import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

import unmask
import unmask.cli as cli_module
from tests.helpers import EXAMPLES
from unmask.backend import JudgeResponse
from unmask.cli import main
from unmask.config import TargetConfig, load_guardrail
from unmask.diagnosis import RegexManifest
from unmask.importer import load_violations
from unmask.scanning import load_taxonomy_regex_examples


def _read_json_mapping(path: Path) -> dict[str, object]:
  loaded: object = cast("object", json.loads(path.read_text(encoding="utf-8")))
  if not isinstance(loaded, dict):
    raise TypeError(f"{path} must contain a JSON object")
  return cast("dict[str, object]", loaded)


def _read_json_list(path: Path) -> list[object]:
  loaded: object = cast("object", json.loads(path.read_text(encoding="utf-8")))
  if not isinstance(loaded, list):
    raise TypeError(f"{path} must contain a JSON list")
  return cast("list[object]", loaded)


class CatalogBackend:
  def __init__(self, config: TargetConfig) -> None:
    del config

  def judge(self, prompt: str, *, max_tokens: int = 8192) -> JudgeResponse:
    del prompt, max_tokens
    return JudgeResponse(
      json.dumps(
        {
          "predicates": {
            "neutral_reporting": "The response reports a third-party action.",
            "financial_education": "The response provides general education.",
            "personalized_recommendation": "The response recommends a transaction.",
            "mentions_investment_term": "The response contains an investment term.",
            "actionable_trading": "The response gives actionable trading steps.",
            "minor": "The user is a minor.",
            "first_time_investor": "The user is investing for the first time.",
          },
          "safe_condition": [
            [{"predicate": "neutral_reporting", "value": True}],
            [{"predicate": "financial_education", "value": True}],
          ],
          "rules": [
            {
              "rule_id": "rule:1",
              "decision": "block",
              "condition": [
                [
                  {"predicate": "neutral_reporting", "value": True},
                  {"predicate": "mentions_investment_term", "value": True},
                ]
              ],
            },
            {
              "rule_id": "rule:2",
              "decision": "allow",
              "condition": [[{"predicate": "financial_education", "value": True}]],
            },
            {
              "rule_id": "rule:3",
              "decision": "block",
              "condition": [
                [
                  {"predicate": "financial_education", "value": True},
                  {"predicate": "actionable_trading", "value": True},
                ]
              ],
            },
            {
              "rule_id": "rule:4",
              "decision": "block",
              "condition": [
                [
                  {"predicate": "personalized_recommendation", "value": True},
                  {"predicate": "minor", "value": True},
                ],
                [
                  {"predicate": "personalized_recommendation", "value": True},
                  {"predicate": "first_time_investor", "value": True},
                ],
              ],
            },
          ],
          "exclusions": [
            [
              "neutral_reporting",
              "financial_education",
              "personalized_recommendation",
            ]
          ],
          "ambiguities": [
            {
              "rule_id": "rule:4",
              "category": "coordination_scope",
              "excerpt": "minors and first-time investors",
              "rationale": "And may join two audiences or require both traits.",
              "interpretation_a": {
                "label": "either protected audience",
                "decision": "block",
                "condition": [
                  [
                    {"predicate": "personalized_recommendation", "value": True},
                    {"predicate": "minor", "value": True},
                  ],
                  [
                    {"predicate": "personalized_recommendation", "value": True},
                    {"predicate": "first_time_investor", "value": True},
                  ],
                ],
                "rationale": "A recommendation to either audience is blocked.",
              },
              "interpretation_b": {
                "label": "both protected audiences",
                "decision": "block",
                "condition": [
                  [
                    {"predicate": "personalized_recommendation", "value": True},
                    {"predicate": "minor", "value": True},
                    {"predicate": "first_time_investor", "value": True},
                  ]
                ],
                "rationale": "Only a user with both traits is covered.",
              },
            }
          ],
        }
      )
    )


def test_natural_language_example_catalog_loads() -> None:
  guardrail = load_guardrail(EXAMPLES / "guardrail.yaml")

  assert guardrail.name == "financial-information-policy"
  assert len(guardrail.rules) == 4
  assert len(guardrail.examples) == 5


def test_regex_example_catalog_loads() -> None:
  manifest = RegexManifest.from_yaml(EXAMPLES / "regexes.yaml")

  assert manifest.name == "financial-information-policy"
  assert len(manifest.rules) == 4
  assert len(manifest.examples) == 5
  assert len(manifest.rules[-1].interpretations) == 2


def test_taxonomy_contributes_a_distinct_regex_example() -> None:
  examples = load_taxonomy_regex_examples(EXAMPLES / "taxonomy.yaml")

  assert len(examples) == 1
  assert examples[0].source == "taxonomy"
  assert examples[0].content == "Summarize how index funds spread investment risk."


def test_violation_catalog_loads_three_canonical_incidents() -> None:
  violations = load_violations(EXAMPLES / "violations.json")

  assert [violation.value for violation in violations] == ["invest", "trade", "buy"]
  assert all(violation.expected_reason for violation in violations)


def test_regex_scan_catalog_demonstrates_each_defect(
  tmp_path: Path,
) -> None:
  output_directory = tmp_path / "scan"

  exit_code = main(
    [
      "scan",
      str(EXAMPLES / "regexes.yaml"),
      "--taxonomy",
      str(EXAMPLES / "taxonomy.yaml"),
      "--output",
      str(output_directory),
    ]
  )

  assert exit_code == 0
  scan = _read_json_mapping(output_directory / "scan.json")
  summary = cast("dict[str, object]", scan["summary"])
  assert summary["findings"] == 3
  assert summary["taxonomyExamples"] == 1
  cases = cast("list[dict[str, object]]", scan["cases"])
  finding_contracts: list[tuple[object, tuple[str, ...]]] = []
  for case in cases:
    diagnosis = cast("dict[str, object]", case["diagnosis"])
    findings = cast("list[dict[str, object]]", diagnosis["findings"])
    for finding in findings:
      rule_ids = cast("list[str]", finding["ruleIds"])
      finding_contracts.append((finding["type"], tuple(rule_ids)))
  assert finding_contracts == [
    ("over_strict", ("block-investment-term",)),
    (
      "contradiction",
      ("allow-how-to-education", "block-actionable-trading"),
    ),
    ("ambiguity", ("first-time-investor-scope",)),
  ]


def test_diagnosis_catalog_isolates_one_defect_per_incident(tmp_path: Path) -> None:
  output_directory = tmp_path / "diagnose"

  exit_code = main(
    [
      "diagnose",
      str(EXAMPLES / "regexes.yaml"),
      str(EXAMPLES / "violations.json"),
      "--output",
      str(output_directory),
    ]
  )

  assert exit_code == 0
  finding_types: list[str] = []
  expected_reasons: list[str] = []
  for index in range(1, 4):
    report = _read_json_mapping(
      output_directory / "incidents" / f"{index:04d}" / "report.json"
    )
    findings = cast("list[dict[str, object]]", report["findings"])
    assert len(findings) == 1
    finding_types.append(cast("str", findings[0]["type"]))
    incident = cast("dict[str, object]", report["incident"])
    expected_reasons.append(cast("str", incident["expectedReason"]))
  assert finding_types == ["over_strict", "contradiction", "ambiguity"]
  assert expected_reasons == [
    "Neutral reporting about a third-party company action is allowed.",
    "Actionable instructions for a specific investment are blocked.",
    "The intended policy blocks recommendations for either protected audience.",
  ]
  assert (output_directory / "index.html").exists()


def test_natural_language_catalog_demonstrates_each_defect(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(cli_module, "create_backend", CatalogBackend)
  output_directory = tmp_path / "scan"

  exit_code = main(
    [
      "scan",
      str(EXAMPLES / "guardrail.yaml"),
      "--taxonomy",
      str(EXAMPLES / "taxonomy.yaml"),
      "--output",
      str(output_directory),
    ]
  )

  assert exit_code == 0
  findings = cast(
    "list[dict[str, object]]",
    _read_json_list(output_directory / "findings.json"),
  )
  assert {cast("str", finding["type"]) for finding in findings} == {
    "over_strict",
    "contradiction",
    "ambiguity",
  }
  ambiguity = next(finding for finding in findings if finding["type"] == "ambiguity")
  metadata = cast("dict[str, object]", ambiguity["metadata"])
  assert metadata["ambiguityRationale"]
  assert metadata["interpretationARationale"]
  assert metadata["interpretationBRationale"]


@pytest.mark.parametrize(
  "script", ["bugs.py", "adherence.py", "benchmarks.py", "ifbench.py", "claims.py"]
)
def test_local_api_examples_run_outside_the_checkout(
  script: str, tmp_path: Path
) -> None:
  result = subprocess.run(
    [sys.executable, str(EXAMPLES / script)],
    cwd=tmp_path,
    check=True,
    capture_output=True,
    text=True,
    timeout=60,
  )
  if script == "bugs.py":
    assert "Regex specification:" in result.stdout
    assert all(
      kind in result.stdout for kind in ("over_strict", "contradiction", "ambiguity")
    )
  elif script == "claims.py":
    assert "VIOLATED" in result.stdout
    assert "grounding ['role']" in result.stdout
  elif script == "benchmarks.py":
    assert "not a published benchmark score" in result.stdout
    assert "denominator=1" in result.stdout
    assert "Changed decisions: 0" in result.stdout
  elif script == "ifbench.py":
    assert "not an official IFBench score" in result.stdout
    assert "Target: unmask.adherence" in result.stdout
    assert "supported: SATISFIED / scored" in result.stdout
    assert "incorrect-judge: VIOLATED / scored" in result.stdout
    assert "insufficient-evidence: UNKNOWN / unknown" in result.stdout
    assert "denominator=2" in result.stdout
  else:
    assert "JSON replay: OK / SATISFIED" in result.stdout
    assert "Python case: OK / SATISFIED" in result.stdout
    assert "Scope:" in result.stdout


def test_model_api_example_uses_the_public_api_without_live_calls(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  find_bugs = unmask.bugs
  calls: list[unmask.TargetConfig] = []

  def run_bugs(
    specification_text: str,
    *,
    specification_name: str,
    taxonomy_text: str,
    model: unmask.TargetConfig,
  ) -> unmask.ScanResult:
    calls.append(model)
    return find_bugs(
      specification_text,
      specification_name=specification_name,
      taxonomy_text=taxonomy_text,
      model=model,
      backend_factory=CatalogBackend,
    )

  monkeypatch.setattr(unmask, "bugs", run_bugs)
  _ = runpy.run_path(str(EXAMPLES / "bugs_with_model.py"), run_name="__main__")
  assert len(calls) == 1
  assert calls[0].provider == "openai"
  output = capsys.readouterr().out
  assert "Natural-language specification:" in output
  assert all(kind in output for kind in ("over_strict", "contradiction", "ambiguity"))
