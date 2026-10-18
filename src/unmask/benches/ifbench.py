"""Local IFBench dataset mapping with a separately supplied scoring protocol."""

import hashlib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import cast

from ..benchmarks import (
  Benchmark,
  BenchmarkCase,
  BenchmarkKind,
  CaseResult,
  Metric,
  Score,
  mean_score,
  parse_json_object,
  require_fields,
  require_items,
  require_object,
  require_text,
)

IFBENCH_SHA256 = "d2ada7da94a38cfe406351614c4e686846ed2da6d1b339db95fa5ead19554a4a"


def _decode_prompt(inputs: dict[str, object]) -> str:
  require_fields(inputs, {"prompt"})
  return require_text(inputs["prompt"], "prompt")


def _encode_response(response: str) -> dict[str, object]:
  if not isinstance(cast("object", response), str):
    raise ValueError("model response must be text")
  return {"response": response}


def IFBench(
  *,
  data_dir: Path,
  score: Callable[[dict[str, object], dict[str, object], dict[str, object]], Score],
  scoring_id: str,
  dataset_sha256: str = IFBENCH_SHA256,
  aggregate: Callable[[tuple[CaseResult, ...]], tuple[Metric, ...]] = mean_score,
) -> Benchmark[str, str]:
  """Define an experiment on local IFBench JSONL using a caller-supplied scorer.

  Reads `IFBench_test.jsonl` only when freezing or evaluating. The default hash
  pins the official single-turn dataset; a custom hash identifies local variants.
  Model callables receive only the prompt. Instruction IDs and parameters remain
  in the scorer reference. This factory provides no official scoring procedure.

  Raises:
    ValueError: if configuration, checksum, or source rows are invalid.
    OSError: when loading data fails during freeze/evaluate.
  """
  if not isinstance(cast("object", data_dir), Path):
    raise ValueError("data_dir must be a pathlib.Path")
  if (
    not isinstance(cast("object", dataset_sha256), str)
    or len(dataset_sha256) != 64
    or any(character not in "0123456789abcdef" for character in dataset_sha256)
  ):
    raise ValueError("dataset_sha256 must be a lowercase SHA-256 digest")
  source = data_dir / "IFBench_test.jsonl"

  def load() -> Iterable[BenchmarkCase]:
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != dataset_sha256:
      raise ValueError(f"{source}: dataset checksum mismatch")
    for line_number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
      try:
        row = parse_json_object(line)
        require_fields(row, {"key", "prompt", "instruction_id_list", "kwargs"})
        identifier = require_text(row["key"], "key")
        prompt = require_text(row["prompt"], "prompt")
        instructions = require_items(row["instruction_id_list"])
        parameters = require_items(row["kwargs"])
        if not instructions or len(instructions) != len(parameters):
          raise ValueError("instruction IDs and kwargs must be non-empty and aligned")
        for instruction, arguments in zip(instructions, parameters, strict=True):
          _ = require_text(instruction, "instruction ID")
          _ = require_object(arguments)
        yield BenchmarkCase(
          identifier,
          "ifbench",
          {"prompt": prompt},
          {"instruction_id_list": instructions, "kwargs": parameters},
        )
      except ValueError as error:
        raise ValueError(f"{source}:{line_number}: {error}") from error

  return Benchmark(
    name="ifbench-data",
    dataset_revision=f"sha256:{dataset_sha256}",
    scoring_id=scoring_id,
    kind=BenchmarkKind.RESPONSE_COMPLIANCE,
    load=load,
    decode_input=_decode_prompt,
    encode_output=_encode_response,
    score=score,
    aggregate=aggregate,
  )
