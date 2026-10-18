"""Reusable local benchmark definitions, frozen samples, and comparable runs."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import platform
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from importlib.metadata import version
from typing import cast

__all__ = [
  "Benchmark",
  "BenchmarkCase",
  "BenchmarkKind",
  "BenchmarkRun",
  "BenchmarkTarget",
  "CaseResult",
  "FrozenBenchmark",
  "Metric",
  "Outcome",
  "Score",
  "mean_score",
]

logger = logging.getLogger(__name__)


class BenchmarkKind(StrEnum):
  """The semantic contract a target agrees to execute."""

  RESPONSE_COMPLIANCE = "response_compliance"
  JUDGE_AUDIT = "judge_audit"
  BUG_DETECTION = "bug_detection"


class Outcome(StrEnum):
  """Whether a case has a usable reference score."""

  SCORED = "scored"
  UNKNOWN = "unknown"
  UNSUPPORTED = "unsupported"
  ERROR = "error"


def require_text(value: object, name: str) -> str:
  if not isinstance(value, str) or not value.strip():
    raise ValueError(f"{name} must be non-empty text")
  try:
    _ = value.encode("utf-8")
  except UnicodeEncodeError as error:
    raise ValueError(f"{name} must be valid UTF-8 text") from error
  return value


def require_object(value: object) -> dict[str, object]:
  if not isinstance(value, dict) or not all(
    isinstance(key, str) for key in cast("dict[object, object]", value)
  ):
    raise ValueError("expected an object with string keys")
  return cast("dict[str, object]", value)


def _validate_json(value: object) -> None:
  if isinstance(value, dict):
    for item in require_object(cast("object", value)).values():
      _validate_json(item)
  elif isinstance(value, list):
    for item in cast("list[object]", value):
      _validate_json(item)
  elif value is not None and type(value) not in (str, bool, int, float):
    raise ValueError(f"not a JSON value: {type(value).__name__}")


def _json(value: object) -> str:
  try:
    # The encoder detects cycles; validation rejects silently coerced keys/tuples.
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    _ = encoded.encode("utf-8")
    _validate_json(value)
  except (TypeError, RecursionError, UnicodeEncodeError) as error:
    raise ValueError(
      "payload must contain finite, acyclic UTF-8 JSON values"
    ) from error
  return encoded


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
  result: dict[str, object] = {}
  for key, value in pairs:
    if key in result:
      raise ValueError(f"duplicate JSON field: {key}")
    result[key] = value
  return result


def parse_json_object(text: str) -> dict[str, object]:
  if not isinstance(cast("object", text), str):
    raise ValueError("serialized benchmark data must be text")
  try:
    value = cast("object", json.loads(text, object_pairs_hook=_unique_object))
  except RecursionError as error:
    raise ValueError("JSON nesting is too deep") from error
  result = require_object(value)
  _ = _json(result)
  return result


def require_fields(data: dict[str, object], names: set[str]) -> None:
  if set(data) != names:
    raise ValueError(f"expected fields {sorted(names)}; got {sorted(data)}")


def require_items(value: object) -> list[object]:
  if not isinstance(value, list):
    raise ValueError("expected a list")
  return cast("list[object]", value)


def _number(value: object, name: str) -> float:
  if type(value) not in (int, float):
    raise ValueError(f"{name} must be a finite number")
  try:
    number = cast("int | float", value)
    finite = math.isfinite(number)
  except OverflowError as error:
    raise ValueError(f"{name} must be a finite number") from error
  if not finite:
    raise ValueError(f"{name} must be a finite number")
  return number


@dataclass(frozen=True, init=False)
class BenchmarkCase:
  """A stable ID, task label, target input, and separately held reference.

  Payloads are detached on input and on access, so targets cannot mutate a replay.

  Raises:
    ValueError: if IDs are empty or payloads are not finite JSON objects.
  """

  id: str
  task: str
  _input_json: str = field(repr=False)
  _reference_json: str = field(repr=False)

  def __init__(
    self, id: str, task: str, inputs: dict[str, object], reference: dict[str, object]
  ) -> None:
    object.__setattr__(self, "id", require_text(id, "case id"))
    object.__setattr__(self, "task", require_text(task, "task"))
    object.__setattr__(self, "_input_json", _json(require_object(inputs)))
    object.__setattr__(self, "_reference_json", _json(require_object(reference)))

  @property
  def inputs(self) -> dict[str, object]:
    # Stored snapshots are validated at construction; decoding detaches caller edits.
    return cast("dict[str, object]", json.loads(self._input_json))

  @property
  def reference(self) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(self._reference_json))

  def to_dict(self) -> dict[str, object]:
    return {
      "id": self.id,
      "task": self.task,
      "inputs": self.inputs,
      "reference": self.reference,
    }

  @classmethod
  def from_dict(cls, value: object) -> BenchmarkCase:
    """Decode a saved case.

    Raises:
      ValueError: if the record is malformed.
    """
    data = require_object(value)
    require_fields(data, {"id", "task", "inputs", "reference"})
    return cls(
      require_text(data["id"], "id"),
      require_text(data["task"], "task"),
      require_object(data["inputs"]),
      require_object(data["reference"]),
    )


@dataclass(frozen=True)
class Score:
  """A bounded reference score, or an explicit reason it is unavailable.

  `decision` is the scorer's stable decision label used in paired comparisons.

  Raises:
    ValueError: if a score is non-finite, out of range, or inconsistent with status.
  """

  outcome: Outcome
  value: float | None = None
  decision: str | None = None
  reason: str = ""

  def __post_init__(self) -> None:
    if not isinstance(cast("object", self.outcome), Outcome):
      raise ValueError("outcome must be an Outcome")
    if self.decision is not None:
      _ = require_text(self.decision, "decision")
    if not isinstance(cast("object", self.reason), str):
      raise ValueError("reason must be text")
    if self.reason.strip():
      _ = require_text(self.reason, "reason")
    if self.outcome == Outcome.SCORED:
      if self.value is None or not 0 <= _number(self.value, "score") <= 1:
        raise ValueError("scored values must be in [0, 1]")
      _ = require_text(self.decision, "scored decision")
    elif self.value is not None or not self.reason.strip():
      raise ValueError("unresolved cases need a reason and no numeric score")
    if self.outcome == Outcome.ERROR and self.decision is not None:
      raise ValueError("execution errors cannot assert a decision")

  def to_dict(self) -> dict[str, object]:
    return {
      "outcome": self.outcome.value,
      "value": self.value,
      "decision": self.decision,
      "reason": self.reason,
    }


@dataclass(frozen=True)
class Metric:
  """An aggregate with its actual denominator; zero denominators have no value.

  Raises:
    ValueError: if the value or denominator is invalid.
  """

  name: str
  value: float | None
  denominator: int

  def __post_init__(self) -> None:
    _ = require_text(self.name, "metric name")
    if type(self.denominator) is not int or self.denominator < 0:
      raise ValueError("denominator must be a nonnegative integer")
    if (self.value is None) != (self.denominator == 0):
      raise ValueError("a metric has no value exactly when its denominator is zero")
    if self.value is not None:
      _ = _number(self.value, "metric value")


@dataclass(frozen=True)
class CaseResult:
  """One output and reference assessment, including isolated execution failures.

  Raises:
    ValueError: if timing, output, or failure phase is invalid.
  """

  case_id: str
  score: Score
  output_json: str | None
  elapsed_seconds: float
  error_phase: str | None = None

  def __post_init__(self) -> None:
    _ = require_text(self.case_id, "case id")
    if not isinstance(cast("object", self.score), Score):
      raise ValueError("score must be a Score")
    if _number(self.elapsed_seconds, "elapsed_seconds") < 0:
      raise ValueError("elapsed_seconds cannot be negative")
    if self.output_json is not None:
      _ = parse_json_object(self.output_json)
    if (self.score.outcome == Outcome.ERROR) != (self.error_phase is not None):
      raise ValueError("errors must record their execution phase")
    if self.error_phase not in (None, "target", "scorer"):
      raise ValueError("invalid error phase")
    if self.error_phase == "target" and self.output_json is not None:
      raise ValueError("target errors cannot retain an encoded output")
    if self.error_phase != "target" and self.output_json is None:
      raise ValueError("completed targets must retain an output")

  @property
  def output(self) -> dict[str, object] | None:
    return (
      cast("dict[str, object]", json.loads(self.output_json))
      if self.output_json is not None
      else None
    )


@dataclass(frozen=True)
class FrozenBenchmark:
  """The complete selected sample and identities needed for loader-free replay.

  Raises:
    ValueError: if identities or cases are invalid or case IDs are duplicated.
  """

  name: str
  dataset_revision: str
  scoring_id: str
  kind: BenchmarkKind
  cases: tuple[BenchmarkCase, ...]

  def __post_init__(self) -> None:
    for name in ("name", "dataset_revision", "scoring_id"):
      _ = require_text(cast("object", getattr(self, name)), name)
    if not isinstance(cast("object", self.kind), BenchmarkKind):
      raise ValueError("kind must be a BenchmarkKind")
    if (
      not isinstance(cast("object", self.cases), tuple)
      or not self.cases
      or not all(isinstance(cast("object", case), BenchmarkCase) for case in self.cases)
    ):
      raise ValueError("cases must be a non-empty tuple of BenchmarkCase")
    if len({case.id for case in self.cases}) != len(self.cases):
      raise ValueError("duplicate case ids")

  def _payload(self) -> dict[str, object]:
    return {
      "schema_version": 1,
      "name": self.name,
      "dataset_revision": self.dataset_revision,
      "scoring_id": self.scoring_id,
      "kind": self.kind.value,
      "cases": [case.to_dict() for case in self.cases],
    }

  def to_json(self) -> str:
    return _json({**self._payload(), "fingerprint": self.fingerprint})

  @property
  def fingerprint(self) -> str:
    return hashlib.sha256(_json(self._payload()).encode("utf-8")).hexdigest()

  @classmethod
  def from_json(cls, text: str) -> FrozenBenchmark:
    """Load frozen payloads without consulting a mutable dataset source.

    Raises:
      ValueError: if JSON, schema, metadata, or cases are invalid.
    """
    data = parse_json_object(text)
    require_fields(
      data,
      {
        "schema_version",
        "name",
        "dataset_revision",
        "scoring_id",
        "kind",
        "cases",
        "fingerprint",
      },
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
      raise ValueError("unsupported frozen benchmark schema")
    sample = cls(
      require_text(data["name"], "name"),
      require_text(data["dataset_revision"], "dataset_revision"),
      require_text(data["scoring_id"], "scoring_id"),
      BenchmarkKind(require_text(data["kind"], "kind")),
      tuple(BenchmarkCase.from_dict(item) for item in require_items(data["cases"])),
    )

    if sample.fingerprint != data["fingerprint"]:
      raise ValueError("frozen sample fingerprint mismatch")
    return sample


@dataclass(frozen=True)
class BenchmarkTarget[Input, Output]:
  """A typed callable with an explicit task contract and reproducible identity.

  Raises:
    ValueError: if metadata or callable is invalid.
  """

  name: str
  revision: str
  kind: BenchmarkKind
  call: Callable[[Input], Output] = field(repr=False, compare=False)

  def __post_init__(self) -> None:
    _ = require_text(self.name, "target name")
    _ = require_text(self.revision, "target revision")
    if not isinstance(cast("object", self.kind), BenchmarkKind) or not callable(
      self.call
    ):
      raise ValueError("target needs a BenchmarkKind and a callable")


def mean_score(results: tuple[CaseResult, ...]) -> tuple[Metric, ...]:
  """Average resolved scores only; run counts separately expose excluded cases."""
  values = [result.score.value for result in results if result.score.value is not None]
  return (
    Metric("mean_score", sum(values) / len(values) if values else None, len(values)),
  )


@dataclass(frozen=True)
class BenchmarkRun:
  """Canonical results, explicit counts, frozen inputs, and run metadata.

  Raises:
    ValueError: if results do not match the frozen cases or metadata is invalid.
  """

  frozen: FrozenBenchmark
  target_name: str
  target_revision: str
  results: tuple[CaseResult, ...]
  metrics: tuple[Metric, ...]
  started_at: str
  elapsed_seconds: float
  python_version: str
  unmask_version: str

  def __post_init__(self) -> None:
    for name in (
      "target_name",
      "target_revision",
      "started_at",
      "python_version",
      "unmask_version",
    ):
      _ = require_text(cast("object", getattr(self, name)), name)
    if not isinstance(cast("object", self.frozen), FrozenBenchmark):
      raise ValueError("frozen must be a FrozenBenchmark")
    timestamp = datetime.fromisoformat(self.started_at)
    if timestamp.tzinfo is None:
      raise ValueError("started_at must include a timezone")
    if not isinstance(cast("object", self.results), tuple) or not all(
      isinstance(cast("object", result), CaseResult) for result in self.results
    ):
      raise ValueError("results must be a tuple of CaseResult")
    if tuple(result.case_id for result in self.results) != tuple(
      case.id for case in self.frozen.cases
    ):
      raise ValueError("results must match frozen case ids and order")
    if (
      not self.metrics
      or not isinstance(cast("object", self.metrics), tuple)
      or not all(isinstance(cast("object", metric), Metric) for metric in self.metrics)
    ):
      raise ValueError("metrics must be a non-empty tuple of Metric")
    if len({metric.name for metric in self.metrics}) != len(self.metrics):
      raise ValueError("duplicate metric names")
    if _number(self.elapsed_seconds, "elapsed_seconds") < 0:
      raise ValueError("elapsed_seconds cannot be negative")

  @property
  def counts(self) -> dict[str, int]:
    return {
      "total": len(self.results),
      **{
        outcome.value: sum(result.score.outcome == outcome for result in self.results)
        for outcome in Outcome
      },
    }

  def to_json(self) -> str:
    results: list[dict[str, object]] = []
    for result in self.results:
      record: dict[str, object] = {
        "case_id": result.case_id,
        "score": result.score.to_dict(),
        "output_json": result.output_json,
        "elapsed_seconds": result.elapsed_seconds,
        "error_phase": result.error_phase,
      }
      results.append(record)
    frozen = cast("dict[str, object]", json.loads(self.frozen.to_json()))
    return _json(
      {
        "schema_version": 1,
        "frozen": frozen,
        "target_name": self.target_name,
        "target_revision": self.target_revision,
        "results": results,
        "metrics": [asdict(metric) for metric in self.metrics],
        "started_at": self.started_at,
        "elapsed_seconds": self.elapsed_seconds,
        "python_version": self.python_version,
        "unmask_version": self.unmask_version,
      }
    )

  @classmethod
  def from_json(cls, text: str) -> BenchmarkRun:
    """Restore structurally validated run data and check its sample identity.

    Saved scores and metrics are claims; callbacks are not re-executed here.

    Raises:
      ValueError: if the schema, identity, scores, metrics, or results are invalid.
    """
    data = parse_json_object(text)
    require_fields(
      data,
      {
        "schema_version",
        "frozen",
        "target_name",
        "target_revision",
        "results",
        "metrics",
        "started_at",
        "elapsed_seconds",
        "python_version",
        "unmask_version",
      },
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
      raise ValueError("unsupported benchmark run schema")
    frozen = FrozenBenchmark.from_json(_json(data["frozen"]))
    results: list[CaseResult] = []
    for item in require_items(data["results"]):
      record = require_object(item)
      require_fields(
        record, {"case_id", "score", "output_json", "elapsed_seconds", "error_phase"}
      )
      score = require_object(record["score"])
      require_fields(score, {"outcome", "value", "decision", "reason"})
      decision = (
        None
        if score["decision"] is None
        else require_text(score["decision"], "decision")
      )
      output = (
        None
        if record["output_json"] is None
        else require_text(record["output_json"], "output_json")
      )
      phase = (
        None
        if record["error_phase"] is None
        else require_text(record["error_phase"], "error_phase")
      )
      results.append(
        CaseResult(
          require_text(record["case_id"], "case_id"),
          Score(
            Outcome(require_text(score["outcome"], "outcome")),
            None if score["value"] is None else _number(score["value"], "score"),
            decision,
            cast("str", score["reason"]),
          ),
          output,
          _number(record["elapsed_seconds"], "elapsed_seconds"),
          phase,
        )
      )
    metrics: list[Metric] = []
    for item in require_items(data["metrics"]):
      record = require_object(item)
      require_fields(record, {"name", "value", "denominator"})
      metrics.append(
        Metric(
          require_text(record["name"], "name"),
          None if record["value"] is None else _number(record["value"], "metric"),
          cast("int", record["denominator"]),
        )
      )
    return cls(
      frozen,
      require_text(data["target_name"], "target_name"),
      require_text(data["target_revision"], "target_revision"),
      tuple(results),
      tuple(metrics),
      require_text(data["started_at"], "started_at"),
      _number(data["elapsed_seconds"], "elapsed_seconds"),
      require_text(data["python_version"], "python_version"),
      require_text(data["unmask_version"], "unmask_version"),
    )

  def compare(self, other: BenchmarkRun) -> dict[str, object]:
    """Compare paired decisions and scores only for identical frozen experiments.

    Raises:
      ValueError: if samples, revision, task contract, or scoring identity differ.
    """
    if not isinstance(cast("object", other), BenchmarkRun):
      raise ValueError("comparison requires a BenchmarkRun")
    if self.frozen != other.frozen:
      raise ValueError(
        "paired comparison requires identical frozen cases and scoring identity"
      )
    changes: list[dict[str, object]] = []
    for before, after in zip(self.results, other.results, strict=True):
      if before.score != after.score:
        changes.append(
          {
            "case_id": before.case_id,
            "before": before.score.to_dict(),
            "after": after.score.to_dict(),
          }
        )
    return {
      "fingerprint": self.frozen.fingerprint,
      "paired_cases": len(self.results),
      "changed_decisions": sum(
        before.score.decision != after.score.decision
        for before, after in zip(self.results, other.results, strict=True)
      ),
      "changes": changes,
      "before_counts": self.counts,
      "after_counts": other.counts,
      "before_metrics": [asdict(metric) for metric in self.metrics],
      "after_metrics": [asdict(metric) for metric in other.metrics],
    }


@dataclass(frozen=True)
class Benchmark[Input, Output]:
  """An experiment definition using the same contract for built-in and custom data.

  The loader maps source rows to cases. Decode receives input only; the scorer
  receives fresh input, reference, and serialized output objects. Scoring identity
  covers mapping, reference checks, and aggregation, including any named variant.

  Raises:
    ValueError: if identity fields or callbacks are invalid.
  """

  name: str
  dataset_revision: str
  scoring_id: str
  kind: BenchmarkKind
  load: Callable[[], Iterable[BenchmarkCase]] = field(repr=False, compare=False)
  decode_input: Callable[[dict[str, object]], Input] = field(repr=False, compare=False)
  encode_output: Callable[[Output], dict[str, object]] = field(
    repr=False, compare=False
  )
  score: Callable[[dict[str, object], dict[str, object], dict[str, object]], Score] = (
    field(repr=False, compare=False)
  )
  aggregate: Callable[[tuple[CaseResult, ...]], tuple[Metric, ...]] = field(
    default=mean_score, repr=False, compare=False
  )

  def __post_init__(self) -> None:
    for name in ("name", "dataset_revision", "scoring_id"):
      _ = require_text(cast("object", getattr(self, name)), name)
    if not isinstance(cast("object", self.kind), BenchmarkKind):
      raise ValueError("kind must be a BenchmarkKind")
    if not all(
      callable(callback)
      for callback in (
        self.load,
        self.decode_input,
        self.encode_output,
        self.score,
        self.aggregate,
      )
    ):
      raise ValueError("benchmark callbacks must be callable")

  def freeze(
    self,
    *,
    tasks: tuple[str, ...] | None = None,
    sample_size: int | None = None,
    seed: int = 0,
  ) -> FrozenBenchmark:
    """Validate and save selected cases; sampling is independent of loader order.

    Raises:
      ValueError: if data, selection, or mapped inputs are invalid.
      Exception: loader and decoder failures propagate before target execution.
    """
    cases = tuple(self.load())
    frozen = FrozenBenchmark(
      self.name, self.dataset_revision, self.scoring_id, self.kind, cases
    )
    if type(seed) is not int:
      raise ValueError("seed must be an integer")
    if tasks is not None:
      if (
        not isinstance(cast("object", tasks), tuple)
        or not tasks
        or any(not isinstance(cast("object", task), str) for task in tasks)
        or len(set(tasks)) != len(tasks)
      ):
        raise ValueError("tasks must be a non-empty tuple of distinct task names")
      if set(tasks) - {case.task for case in frozen.cases}:
        raise ValueError("unknown task selection")
      cases = tuple(case for case in cases if case.task in tasks)
    cases = tuple(sorted(cases, key=lambda case: case.id))
    if sample_size is not None:
      if type(sample_size) is not int or not 1 <= sample_size <= len(cases):
        raise ValueError("sample_size must be between 1 and the selected case count")
      cases = tuple(random.Random(seed).sample(cases, sample_size))
    for case in cases:
      _ = self.decode_input(case.inputs)
    return FrozenBenchmark(
      self.name, self.dataset_revision, self.scoring_id, self.kind, cases
    )

  def evaluate(
    self,
    target: BenchmarkTarget[Input, Output] | None = None,
    *,
    model: Callable[[Input], Output] | None = None,
    target_name: str | None = None,
    target_revision: str | None = None,
    frozen: FrozenBenchmark | None = None,
  ) -> BenchmarkRun:
    """Execute locally supplied callbacks, capturing per-case target/scorer errors.

    Replaying passes a saved `frozen` sample and never invokes the loader.
    Supply either a target or a callable model with explicit name and revision.
    Aggregation failures raise rather than returning misleading partial metrics.

    Raises:
      ValueError: if target or frozen identity is incompatible, or inputs invalid.
      Exception: loader, input validation, and aggregation failures propagate.
    """
    if model is not None:
      if target is not None:
        raise ValueError("supply either target or model, not both")
      target = BenchmarkTarget(
        require_text(target_name, "target_name"),
        require_text(target_revision, "target_revision"),
        self.kind,
        model,
      )
    elif target_name is not None or target_revision is not None:
      raise ValueError("target_name and target_revision require model")
    if not isinstance(cast("object", target), BenchmarkTarget):
      raise ValueError("target must be a BenchmarkTarget with a declared task contract")
    target = cast("BenchmarkTarget[Input, Output]", target)
    if frozen is not None and not isinstance(cast("object", frozen), FrozenBenchmark):
      raise ValueError("frozen must be a FrozenBenchmark")
    if target.kind != self.kind:
      raise ValueError("target task contract is incompatible with this benchmark")
    unmask_version = version("unmask")
    sample = self.freeze() if frozen is None else frozen
    if (sample.name, sample.dataset_revision, sample.scoring_id, sample.kind) != (
      self.name,
      self.dataset_revision,
      self.scoring_id,
      self.kind,
    ):
      raise ValueError("frozen benchmark identity mismatch")
    if frozen is not None:
      for case in sample.cases:
        _ = self.decode_input(case.inputs)
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    results: list[CaseResult] = []
    for case in sample.cases:
      case_started = time.perf_counter()
      output_json: str | None = None
      phase = "target"
      error_phase: str | None = None
      try:
        output = target.call(self.decode_input(case.inputs))
        output_json = _json(require_object(self.encode_output(output)))
        phase = "scorer"
        score = self.score(case.inputs, case.reference, parse_json_object(output_json))
        if (
          not isinstance(cast("object", score), Score) or score.outcome == Outcome.ERROR
        ):
          raise ValueError(
            "scorer must return a Score; ERROR is reserved for execution failures"
          )
      except Exception as error:
        # User callbacks form an isolation boundary; preserve every failure in the run.
        logger.debug(
          "Benchmark %s target %s case %s failed during %s",
          self.name,
          target.name,
          case.id,
          phase,
          exc_info=True,
        )
        reason = f"{type(error).__name__}: {error}".encode(
          "utf-8", errors="backslashreplace"
        ).decode("utf-8")
        score = Score(Outcome.ERROR, reason=reason)
        error_phase = phase
      results.append(
        CaseResult(
          case.id, score, output_json, time.perf_counter() - case_started, error_phase
        )
      )
    completed = tuple(results)
    metrics = self.aggregate(completed)
    return BenchmarkRun(
      sample,
      target.name,
      target.revision,
      completed,
      metrics,
      started_at,
      time.perf_counter() - started,
      platform.python_version(),
      unmask_version,
    )
