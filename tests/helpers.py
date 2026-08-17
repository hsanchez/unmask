from pathlib import Path

import yaml


def write_yaml(path: Path, data: object) -> Path:
  _ = path.write_text(yaml.safe_dump(data), encoding="utf-8")
  return path
