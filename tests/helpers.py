import json
from pathlib import Path
from typing import cast

import yaml
from werkzeug.test import TestResponse

from unmask.backend import JudgeResponse
from unmask.config import TargetConfig

REPLAY_FIXTURES = Path(__file__).resolve().parent / "fixtures/judge_audit"
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def write_yaml(path: Path, data: object) -> Path:
  _ = path.write_text(yaml.safe_dump(data), encoding="utf-8")
  return path


def json_object(value: object) -> dict[str, object]:
  if not isinstance(value, dict):
    raise TypeError(f"expected a JSON object, got {type(value).__name__}")
  return cast("dict[str, object]", value)


def json_list(value: object) -> list[object]:
  if not isinstance(value, list):
    raise TypeError(f"expected a JSON array, got {type(value).__name__}")
  return cast("list[object]", value)


def json_text(value: object) -> str:
  if not isinstance(value, str):
    raise TypeError(f"expected a JSON string, got {type(value).__name__}")
  return value


def json_integer(value: object) -> int:
  if not isinstance(value, int) or isinstance(value, bool):
    raise TypeError(f"expected a JSON integer, got {type(value).__name__}")
  return value


def load_json_object(text: str) -> dict[str, object]:
  return json_object(cast("object", json.loads(text)))


def present[Value](value: Value | None) -> Value:
  if value is None:
    raise AssertionError("expected a value, got None")
  return value


def response_object(response: TestResponse) -> dict[str, object]:
  return json_object(cast("object", response.get_json()))


def replay_text(prefix: str = "A_") -> str:
  path = next(REPLAY_FIXTURES.glob(f"{prefix}*"))
  return path.read_text(encoding="utf-8")


def replay_object(prefix: str = "A_") -> dict[str, object]:
  return load_json_object(replay_text(prefix))


class ConflictingRulesBackend:
  def judge(self, prompt: str, *, max_tokens: int = 8192) -> JudgeResponse:
    del prompt, max_tokens
    return JudgeResponse(
      json.dumps(
        {
          "predicates": {"is_safe": "request is safe"},
          "safe_condition": [[{"predicate": "is_safe", "value": True}]],
          "rules": [
            {
              "rule_id": "rule:1",
              "decision": "block",
              "condition": [[{"predicate": "is_safe", "value": True}]],
              "rationale": "The rule blocks safe content.",
            },
            {
              "rule_id": "rule:2",
              "decision": "allow",
              "condition": [[{"predicate": "is_safe", "value": True}]],
              "rationale": "The rule allows safe content.",
            },
          ],
          "ambiguities": [
            {
              "rule_id": "rule:1",
              "category": "scope",
              "excerpt": "all advice",
              "rationale": "The scope is unclear.",
              "interpretation_a": {
                "label": "broad",
                "decision": "block",
                "condition": [[{"predicate": "is_safe", "value": True}]],
                "rationale": "All content is included.",
              },
              "interpretation_b": {
                "label": "narrow",
                "decision": "block",
                "condition": [[{"predicate": "is_safe", "value": False}]],
                "rationale": "Only unsafe content is included.",
              },
            }
          ],
        }
      )
    )


def target_config() -> TargetConfig:
  return TargetConfig(
    project=None,
    location="global",
    judge_model="fake",
    thinking_budget=0,
  )
