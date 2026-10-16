import json
from pathlib import Path
from typing import cast

import yaml
from werkzeug.test import TestResponse

REPLAY_FIXTURES = Path(__file__).resolve().parent / "fixtures/judge_audit"


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
