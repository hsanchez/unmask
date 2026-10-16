"""Conversation-grounded diagnosis for regex guardrails."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from itertools import combinations
from multiprocessing import get_context
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Self, cast
from typing import Literal as TypingLiteral

import yaml

from .analyzer import SymbolicAnalyzer
from .models import (
  Ambiguity,
  AmbiguityInterpretation,
  BugType,
  Decision,
  Finding,
  Guardrail,
  GuardrailRule,
  Literal,
  RuleSemantics,
  SymbolicModel,
)

if TYPE_CHECKING:
  from multiprocessing.process import BaseProcess

type RegexFlagName = TypingLiteral[
  "ASCII", "DOTALL", "IGNORECASE", "MULTILINE", "VERBOSE"
]
type ExpectedOutcomeSource = TypingLiteral[
  "violation",
  "cli_default",
  "manifest",
  "taxonomy",
  "missing",
]
type RegexExampleSource = TypingLiteral["manifest", "taxonomy"]
type RawRegexMatch = tuple[str, int, int, tuple[str | None, ...]]

_REGEX_FLAGS: dict[RegexFlagName, re.RegexFlag] = {
  "ASCII": re.ASCII,
  "DOTALL": re.DOTALL,
  "IGNORECASE": re.IGNORECASE,
  "MULTILINE": re.MULTILINE,
  "VERBOSE": re.VERBOSE,
}

_DEFAULT_MATCH_TIMEOUT_SECONDS: float = 1.0
_MAX_MATCH_TIMEOUT_SECONDS: float = 60.0
_MAX_MATCHES_PER_PATTERN = 10_000
_WORKER_START_TIMEOUT_SECONDS: float = 5.0
_WORKER_START_ATTEMPTS = 2


class RegexExecutionError(RuntimeError):
  """Raised when exact regex evaluation cannot complete safely."""


class _Connection(Protocol):
  def send(self, obj: object) -> None: ...

  def recv(self) -> object: ...

  def poll(self, timeout: float = 0.0) -> bool: ...

  def close(self) -> None: ...


def _compiled_flags(flags: tuple[RegexFlagName, ...]) -> re.RegexFlag:
  compiled_flags = re.NOFLAG
  for flag in flags:
    compiled_flags |= _REGEX_FLAGS[flag]
  return compiled_flags


def _validate_pattern(
  pattern: str,
  flags: tuple[RegexFlagName, ...],
  name: str,
) -> None:
  try:
    _ = re.compile(pattern, _compiled_flags(flags))
  except re.error as exc:
    raise ValueError(f"invalid regex for {name}: {exc}") from exc


def _validate_match_timeout(value: object) -> float:
  if (
    not isinstance(value, (int, float))
    or isinstance(value, bool)
    or not math.isfinite(value)
    or not 0 < value <= _MAX_MATCH_TIMEOUT_SECONDS
  ):
    raise ValueError(
      "match_timeout_seconds must be a finite number greater than zero "
      + f"and no more than {_MAX_MATCH_TIMEOUT_SECONDS:g}"
    )
  return float(value)


def _match_pattern_worker(
  connection: _Connection,
) -> None:
  connection.send(obj="ready")
  while True:
    request: object = connection.recv()
    if request is None:
      connection.close()
      return
    text, pattern, flags, location_start, location_end = cast(
      "tuple[str, str, tuple[RegexFlagName, ...], int, int]",
      request,
    )
    matches: list[RawRegexMatch] = []
    compiled: re.Pattern[str] = re.compile(pattern, _compiled_flags(flags))
    for match in compiled.finditer(text):
      if match.start() >= location_end or match.end() <= location_start:
        continue
      if len(matches) >= _MAX_MATCHES_PER_PATTERN:
        message: str = (
          f"pattern produced more than {_MAX_MATCHES_PER_PATTERN} relevant matches"
        )
        connection.send(obj=("error", message))
        break
      matches.append((match.group(), match.start(), match.end(), match.groups()))
    else:
      connection.send(obj=("ok", tuple(matches)))


class _RegexWorker:
  def __init__(self) -> None:
    self._connection: _Connection
    self._process: BaseProcess
    self._start()

  def __enter__(self) -> Self:
    return self

  def __exit__(
    self,
    exception_type: object,
    exception: object,
    traceback: object,
  ) -> None:
    self.close()

  def evaluate(
    self,
    incident: ConversationIncident,
    rule_id: str,
    pattern: str,
    flags: tuple[RegexFlagName, ...],
    timeout_seconds: float,
  ) -> tuple[RawRegexMatch, ...]:
    """
    Raises:
      RegexExecutionError: if the match exceeds `timeout_seconds`, the worker
        process dies mid-evaluation, or the worker reports too many matches.
    """
    self._connection.send(
      obj=(
        incident.text,
        pattern,
        flags,
        incident.location.start,
        incident.location.end,
      )
    )
    if not self._connection.poll(timeout=timeout_seconds):
      self._restart()
      raise RegexExecutionError(
        f"regex {rule_id} exceeded the {timeout_seconds:g}-second match timeout"
      )
    try:
      status, payload = cast("tuple[str, object]", self._connection.recv())
    except EOFError as exc:
      raise RegexExecutionError(
        f"regex worker stopped while evaluating {rule_id}"
      ) from exc
    if status == "error":
      raise RegexExecutionError(f"regex {rule_id} {payload}")
    return cast("tuple[RawRegexMatch, ...]", payload)

  def close(self) -> None:
    if self._process.is_alive():
      self._connection.send(obj=None)
      self._process.join(timeout=1)
      if self._process.is_alive():
        self._process.terminate()
        self._process.join()
    self._connection.close()

  def _restart(self) -> None:
    if self._process.is_alive():
      self._process.terminate()
      self._process.join()
    self._connection.close()
    self._start()

  def _start(self) -> None:
    for _ in range(_WORKER_START_ATTEMPTS):
      worker: tuple[_Connection, BaseProcess] | None = self._start_once()
      if worker is not None:
        self._connection, self._process = worker
        return
    raise RegexExecutionError(
      f"regex worker did not start after {_WORKER_START_ATTEMPTS} attempts"
    )

  @staticmethod
  def _start_once() -> tuple[_Connection, BaseProcess] | None:
    process_context = get_context(method="spawn")
    parent_connection, worker_connection = process_context.Pipe()
    process = process_context.Process(
      target=_match_pattern_worker,
      args=(worker_connection,),
    )
    process.start()
    worker_connection.close()
    if not parent_connection.poll(timeout=_WORKER_START_TIMEOUT_SECONDS):
      process.terminate()
      process.join()
      parent_connection.close()
      return None
    try:
      parent_connection.recv()
    except EOFError:
      process.join()
      parent_connection.close()
      return None
    return parent_connection, process


def _mapping(raw: object, name: str) -> dict[str, object]:
  if not isinstance(raw, dict):
    raise TypeError(f"{name} must be a mapping")
  return cast("dict[str, object]", raw)


def _list(raw: object, name: str) -> list[object]:
  if not isinstance(raw, list):
    raise TypeError(f"{name} must be a list")
  return cast("list[object]", raw)


def _is_integer(value: object) -> bool:
  return isinstance(value, int) and not isinstance(value, bool)


def _text(raw: object, name: str) -> str:
  if not isinstance(raw, str) or not raw:
    raise ValueError(f"{name} must be a non-empty string")
  return raw


def _decision(raw: object, name: str, *, required: bool) -> Decision | None:
  if raw is None and not required:
    return None
  if raw not in ("allow", "block"):
    raise ValueError(f"{name} must be 'allow' or 'block'")
  return raw


def _required_decision(raw: object, name: str) -> Decision:
  decision: Decision | None = _decision(raw, name, required=True)
  if decision is None:
    raise ValueError(f"{name} is required")
  return decision


def _flags(raw: object, name: str) -> tuple[RegexFlagName, ...]:
  if raw is None:
    return ()
  result: list[RegexFlagName] = []
  for item in _list(raw, name):
    if item not in _REGEX_FLAGS:
      supported: str = ", ".join(sorted(_REGEX_FLAGS))
      raise ValueError(f"{name} contains unsupported flag {item!r}; use {supported}")
    flag: RegexFlagName = item
    if flag not in result:
      result.append(flag)
  return tuple(result)


@dataclass(frozen=True)
class TextLocation:
  """A half-open `[start, end)` character span within conversation text.

  Raises:
    TypeError: if `start` or `end` is not an integer.
    ValueError: if `start` is negative or `end` is not greater than `start`.
  """

  start: int
  end: int

  def __post_init__(self) -> None:
    if not _is_integer(self.start) or not _is_integer(self.end):
      raise TypeError("conversation.location start and end must be integers")
    if self.start < 0 or self.end <= self.start:
      raise ValueError("conversation.location must satisfy 0 <= start < end")

  @classmethod
  def from_dict(cls, raw: object) -> TextLocation:
    """
    Raises:
      TypeError: if `raw` is not a mapping, or `start`/`end` is not an integer.
    """
    data: dict[str, object] = _mapping(raw, name="conversation.location")
    start: object | None = data.get("start")
    end: object | None = data.get("end")
    if not _is_integer(start) or not _is_integer(end):
      raise TypeError("conversation.location start and end must be integers")
    return cls(start=cast("int", start), end=cast("int", end))

  def to_dict(self) -> dict[str, int]:
    return {"start": self.start, "end": self.end}


@dataclass(frozen=True)
class ConversationIncident:
  """One agent-output incident: the full text, the flagged value, and its expected label.

  Raises:
    ValueError: if `source_location`, `expected_reason`, `expected_outcome`,
      or `expected_outcome_source` violate their required pairing, or if
      `location`/`value` are inconsistent with `text` (see `validate`).
  """

  text: str
  value: str
  location: TextLocation
  source_location: int | None = None
  observed_outcome: Decision | None = None
  expected_outcome: Decision | None = None
  expected_reason: str | None = None
  expected_outcome_source: ExpectedOutcomeSource = "missing"

  def __post_init__(self) -> None:
    if self.source_location is not None and (
      not _is_integer(self.source_location) or self.source_location < 0
    ):
      raise ValueError("conversation.source_location must be a non-negative integer")
    if self.expected_reason is not None and not self.expected_reason.strip():
      raise ValueError("conversation.expected_reason must be a non-empty string")
    if self.expected_outcome is None and self.expected_reason is not None:
      raise ValueError(
        "conversation.expected_outcome is required when expected_reason is supplied"
      )
    if self.expected_outcome is None and self.expected_outcome_source != "missing":
      raise ValueError(
        "conversation.expected_outcome_source must be missing "
        + "when no expected label is supplied"
      )
    self.validate()

  @classmethod
  def from_dict(cls, raw: object) -> ConversationIncident:
    """
    Raises:
      TypeError: if `raw` or `location` is not a mapping.
      ValueError: if a required field is missing or a field has an invalid value.
    """
    data: dict[str, object] = _mapping(raw, name="conversation")
    expected_outcome: Decision | None = _decision(
      raw=data.get("expected_outcome"),
      name="conversation.expected_outcome",
      required=False,
    )
    expected_reason_raw: object | None = data.get("expected_reason")
    expected_reason: str | None = (
      _text(raw=expected_reason_raw, name="conversation.expected_reason")
      if expected_reason_raw is not None
      else None
    )
    source_raw = data.get(
      "expected_outcome_source",
      "violation" if expected_outcome is not None else "missing",
    )
    if source_raw not in (
      "violation",
      "cli_default",
      "manifest",
      "taxonomy",
      "missing",
    ):
      raise ValueError(
        "conversation.expected_outcome_source must be "
        + "'violation', 'cli_default', 'manifest', 'taxonomy', or 'missing'"
      )
    source_location_raw: object | None = data.get("source_location")
    if source_location_raw is not None and not _is_integer(source_location_raw):
      raise ValueError("conversation.source_location must be a non-negative integer")
    return cls(
      text=_text(raw=data.get("text"), name="conversation.text"),
      value=_text(raw=data.get("value"), name="conversation.value"),
      location=TextLocation.from_dict(raw=data.get("location")),
      source_location=cast("int | None", source_location_raw),
      observed_outcome=_decision(
        raw=data.get("observed_outcome"),
        name="conversation.observed_outcome",
        required=False,
      ),
      expected_outcome=expected_outcome,
      expected_reason=expected_reason,
      expected_outcome_source=source_raw,
    )

  def validate(self) -> None:
    """
    Raises:
      ValueError: if `location` exceeds `text`, or the text at `location`
        does not equal `value`.
    """
    if self.location.end > len(self.text):
      raise ValueError("conversation.location exceeds conversation.text")
    located_value: str = self.text[self.location.start : self.location.end]
    if located_value != self.value:
      raise ValueError(
        "conversation.value does not match conversation.text at location"
      )

  def to_dict(self) -> dict[str, object]:
    return {
      "text": self.text,
      "value": self.value,
      "location": self.location.to_dict(),
      "sourceLocation": self.source_location,
      "observedOutcome": self.observed_outcome,
      "expectedOutcome": self.expected_outcome,
      "expectedReason": self.expected_reason,
      "expectedOutcomeSource": self.expected_outcome_source,
    }


@dataclass(frozen=True)
class RegexInterpretation:
  """One labeled, regex-backed reading of a rule with two plausible interpretations."""

  label: str
  pattern: str
  action: Decision

  @classmethod
  def from_dict(cls, raw: object, name: str) -> RegexInterpretation:
    """
    Raises:
      TypeError: if `raw` is not a mapping.
      ValueError: if `label` or `pattern` is empty, or `action` is invalid.
    """
    data: dict[str, object] = _mapping(raw, name)
    return cls(
      label=_text(raw=data.get("label"), name=f"{name}.label"),
      pattern=_text(raw=data.get("pattern"), name=f"{name}.pattern"),
      action=_required_decision(raw=data.get("action"), name=f"{name}.action"),
    )

  def to_dict(self) -> dict[str, str]:
    return {
      "label": self.label,
      "pattern": self.pattern,
      "action": self.action,
    }


@dataclass(frozen=True)
class RegexRule:
  """An executable regex guardrail rule, with an action and optional dual interpretations.

  Raises:
    ValueError: if `pattern`, or an interpretation's pattern, is not a valid
      regex under `flags`.
  """

  id: str
  pattern: str
  action: Decision
  flags: tuple[RegexFlagName, ...] = ()
  description: str = ""
  interpretations: tuple[RegexInterpretation, ...] = ()

  def __post_init__(self) -> None:
    _validate_pattern(self.pattern, self.flags, name=self.id)
    for interpretation in self.interpretations:
      _validate_pattern(interpretation.pattern, self.flags, name=interpretation.label)

  @classmethod
  def from_dict(cls, raw: object, index: int) -> RegexRule:
    """
    Raises:
      TypeError: if `raw` is not a mapping, or `description` is not a string.
      ValueError: if a required field is missing/invalid, or `interpretations`
        does not contain exactly zero or two entries.
    """
    name: str = f"rules[{index}]"
    data: dict[str, object] = _mapping(raw, name)
    raw_interpretations: list[object] = _list(
      data.get("interpretations", []), f"{name}.interpretations"
    )
    interpretations: tuple[RegexInterpretation, ...] = tuple(
      RegexInterpretation.from_dict(
        raw=item, name=f"{name}.interpretations[{item_index}]"
      )
      for item_index, item in enumerate(raw_interpretations)
    )
    if len(interpretations) not in (0, 2):
      raise ValueError(f"{name}.interpretations must contain exactly two entries")
    description = data.get("description", "")
    if not isinstance(description, str):
      raise TypeError(f"{name}.description must be a string")
    return cls(
      id=_text(raw=data.get("id"), name=f"{name}.id"),
      pattern=_text(raw=data.get("pattern"), name=f"{name}.pattern"),
      action=_required_decision(raw=data.get("action"), name=f"{name}.action"),
      flags=_flags(raw=data.get("flags"), name=f"{name}.flags"),
      description=description.strip(),
      interpretations=interpretations,
    )

  def to_dict(self) -> dict[str, object]:
    result: dict[str, object] = {
      "id": self.id,
      "pattern": self.pattern,
      "action": self.action,
    }
    if self.flags:
      result["flags"] = list(self.flags)
    if self.description:
      result["description"] = self.description
    if self.interpretations:
      result["interpretations"] = [
        interpretation.to_dict() for interpretation in self.interpretations
      ]
    return result


@dataclass(frozen=True)
class RegexExample:
  """A labeled example conversation snippet with its expected regex action."""

  content: str
  expected_action: Decision
  reason: str = ""
  source: RegexExampleSource = "manifest"

  @classmethod
  def from_dict(cls, raw: object, index: int) -> RegexExample:
    """
    Raises:
      TypeError: if `raw` is not a mapping.
      ValueError: if a required field is missing, or `source` is invalid.
    """
    name: str = f"examples[{index}]"
    data: dict[str, object] = _mapping(raw, name)
    source = data.get("source", "manifest")
    if source not in ("manifest", "taxonomy"):
      raise ValueError(f"{name}.source must be 'manifest' or 'taxonomy'")
    return cls(
      content=_text(raw=data.get("content"), name=f"{name}.content"),
      expected_action=_required_decision(
        raw=data.get("expected_action"),
        name=f"{name}.expected_action",
      ),
      reason=_text(raw=data.get("reason", ""), name=f"{name}.reason"),
      source=source,
    )

  def to_dict(self) -> dict[str, str]:
    return {
      "content": self.content,
      "expected_action": self.expected_action,
      "reason": self.reason,
      "source": self.source,
    }


@dataclass(frozen=True)
class RegexManifest:
  """A named, versioned collection of regex rules with optional labeled examples.

  Raises:
    ValueError: if schema fields have the wrong value, `name`/`rules` is
      empty, rule ids collide, examples disagree on `expected_action` for the
      same content, or `match_timeout_seconds` is invalid.
  """

  name: str
  rules: tuple[RegexRule, ...]
  examples: tuple[RegexExample, ...] = ()
  match_timeout_seconds: float = _DEFAULT_MATCH_TIMEOUT_SECONDS
  schema_version: int = 1
  kind: TypingLiteral["regex"] = "regex"
  engine: TypingLiteral["python_re"] = "python_re"
  scope: TypingLiteral["agent_output"] = "agent_output"

  def __post_init__(self) -> None:
    if self.schema_version != 1:
      raise ValueError("regex manifest schema_version must be 1")
    if self.kind != "regex":
      raise ValueError("regex manifest kind must be 'regex'")
    if self.engine != "python_re":
      raise ValueError("regex manifest engine must be 'python_re'")
    if self.scope != "agent_output":
      raise ValueError("regex manifest scope must be 'agent_output'")
    if not self.name.strip():
      raise ValueError("regex manifest name must be a non-empty string")
    if not self.rules:
      raise ValueError("regex manifest rules must be a non-empty list")
    rule_ids: list[str] = [rule.id for rule in self.rules]
    duplicate_ids: list[str] = sorted(
      rule_id for rule_id in set(rule_ids) if rule_ids.count(rule_id) > 1
    )
    if duplicate_ids:
      raise ValueError(f"regex manifest rule IDs must be unique: {duplicate_ids}")
    example_actions: dict[str, Decision] = {}
    for example in self.examples:
      existing_action: Decision | None = example_actions.get(example.content)
      if existing_action is not None and existing_action != example.expected_action:
        raise ValueError(
          "regex manifest contains conflicting expected actions for "
          + f"{example.content!r}"
        )
      example_actions[example.content] = example.expected_action
    _ = _validate_match_timeout(value=self.match_timeout_seconds)

  @classmethod
  def from_text(cls, text: str) -> RegexManifest:
    """
    Raises:
      TypeError: if the document, an example, or `schema_version` has the wrong shape.
      ValueError: if a schema field, `name`, or an example's `source` is invalid.
    """
    return cls.from_dict(cast("object", yaml.safe_load(stream=text)))

  @classmethod
  def from_dict(cls, loaded: object) -> RegexManifest:
    """Validate a decoded regex manifest.

    Raises:
      TypeError: if fields have invalid shapes.
      ValueError: if manifest fields or examples are invalid.
    """
    raw: dict[str, object] = _mapping(
      raw=loaded or {},
      name="regex manifest",
    )
    raw_rules: list[object] = _list(raw.get("rules"), "regex manifest rules")
    raw_examples: list[object] = _list(
      raw.get("examples", []), "regex manifest examples"
    )
    schema_version: object = raw.get("schema_version", 1)
    if not _is_integer(schema_version):
      raise TypeError("regex manifest schema_version must be an integer")
    kind: object = raw.get("kind", "regex")
    engine: object = raw.get("engine", "python_re")
    scope: object = raw.get("scope", "agent_output")
    if kind != "regex":
      raise ValueError("regex manifest kind must be 'regex'")
    if engine != "python_re":
      raise ValueError("regex manifest engine must be 'python_re'")
    if scope != "agent_output":
      raise ValueError("regex manifest scope must be 'agent_output'")
    examples: list[RegexExample] = []
    for index, item in enumerate(raw_examples):
      data: dict[str, object] = _mapping(raw=item, name=f"examples[{index}]")
      if data.get("source", "manifest") != "manifest":
        raise ValueError(
          f"examples[{index}].source must be 'manifest' in a regex manifest"
        )
      examples.append(
        RegexExample.from_dict(raw={**data, "source": "manifest"}, index=index)
      )
    return cls(
      schema_version=cast("int", schema_version),
      kind="regex",
      name=_text(raw=raw.get("name"), name="regex manifest name"),
      engine="python_re",
      scope="agent_output",
      match_timeout_seconds=_validate_match_timeout(
        value=raw.get(
          "match_timeout_seconds",
          _DEFAULT_MATCH_TIMEOUT_SECONDS,
        )
      ),
      rules=tuple(
        RegexRule.from_dict(raw=item, index=index)
        for index, item in enumerate(raw_rules)
      ),
      examples=tuple(examples),
    )

  @classmethod
  def from_yaml(cls, path: str | Path) -> RegexManifest:
    """Load and validate a regex manifest from a YAML file.

    Raises:
      OSError: if `path` cannot be read.
      TypeError: if the document, an example, or `schema_version` has the wrong shape.
      ValueError: if a schema field, `name`, or an example's `source` is invalid.
    """
    return cls.from_text(Path(path).read_text(encoding="utf-8"))

  def to_dict(self) -> dict[str, object]:
    result: dict[str, object] = {
      "schema_version": self.schema_version,
      "kind": self.kind,
      "name": self.name,
      "engine": self.engine,
      "scope": self.scope,
      "match_timeout_seconds": self.match_timeout_seconds,
      "rules": [rule.to_dict() for rule in self.rules],
    }
    if self.examples:
      result["examples"] = [example.to_dict() for example in self.examples]
    return result


@dataclass(frozen=True)
class RegexMatchEvidence:
  """A single exact regex match of a rule (or interpretation) against an incident."""

  rule_id: str
  action: Decision
  pattern: str
  matched_text: str
  start: int
  end: int
  groups: tuple[str | None, ...]
  interpretation: str | None = None

  def to_dict(self) -> dict[str, object]:
    return {
      "ruleId": self.rule_id,
      "action": self.action,
      "pattern": self.pattern,
      "matchedText": self.matched_text,
      "location": {"start": self.start, "end": self.end},
      "groups": list(self.groups),
      "interpretation": self.interpretation,
    }


type InterpretationMatches = dict[
  str, tuple[tuple[RegexMatchEvidence, ...], tuple[RegexMatchEvidence, ...]]
]


@dataclass(frozen=True)
class SymbolicProof:
  """The Z3 witness and rationale backing a diagnosis finding."""

  formula: str
  witness: dict[str, bool]
  rationale: str

  def to_dict(self) -> dict[str, object]:
    return {
      "engine": "z3",
      "formula": self.formula,
      "witness": dict(sorted(self.witness.items())),
      "rationale": self.rationale,
    }


@dataclass(frozen=True)
class CounterexampleExplanation:
  """A natural-language walkthrough of the counterexample a proof produced."""

  what_matched: str
  counterexample: str
  why_violation: str
  recommended_fix: str

  def to_dict(self) -> dict[str, str]:
    return {
      "whatMatched": self.what_matched,
      "counterexample": self.counterexample,
      "whyViolation": self.why_violation,
      "recommendedFix": self.recommended_fix,
    }


@dataclass(frozen=True)
class DiagnosisFinding:
  """A single proven regex guardrail defect, with its Z3 proof and remediation."""

  id: str
  bug_type: BugType
  severity: TypingLiteral["low", "medium", "high"]
  rule_ids: tuple[str, ...]
  summary: str
  explanation: str
  remediation: str
  evidence: tuple[RegexMatchEvidence, ...]
  proof: SymbolicProof
  counterexample_explanation: CounterexampleExplanation
  metadata: dict[str, str]

  def to_dict(self) -> dict[str, object]:
    return {
      "id": self.id,
      "type": self.bug_type,
      "severity": self.severity,
      "ruleIds": list(self.rule_ids),
      "summary": self.summary,
      "explanation": self.explanation,
      "remediation": self.remediation,
      "evidence": [item.to_dict() for item in self.evidence],
      "proof": self.proof.to_dict(),
      "counterexampleExplanation": self.counterexample_explanation.to_dict(),
      "metadata": dict(self.metadata),
    }


@dataclass(frozen=True)
class SkippedAnalysis:
  """One defect category that could not be analyzed for this incident, and why."""

  bug_type: BugType
  reason: str

  def to_dict(self) -> dict[str, str]:
    return {"type": self.bug_type, "reason": self.reason}


@dataclass(frozen=True)
class DiagnosisReport:
  """The full diagnosis result for one conversation incident: matches, findings, and gaps."""

  name: str
  incident: ConversationIncident
  matches: tuple[RegexMatchEvidence, ...]
  findings: tuple[DiagnosisFinding, ...]
  skipped_analyses: tuple[SkippedAnalysis, ...]

  def to_dict(self) -> dict[str, object]:
    if self.findings:
      status = "supported_defect"
    elif self.matches:
      status = "no_supported_defect"
    else:
      status = "no_matching_rule"
    return {
      "schemaVersion": 2,
      "name": self.name,
      "incident": self.incident.to_dict(),
      "summary": {
        "matches": len(self.matches),
        "findings": len(self.findings),
        "overStrict": sum(item.bug_type == "over_strict" for item in self.findings),
        "contradictions": sum(
          item.bug_type == "contradiction" for item in self.findings
        ),
        "ambiguities": sum(item.bug_type == "ambiguity" for item in self.findings),
        "status": status,
      },
      "matches": [item.to_dict() for item in self.matches],
      "findings": [item.to_dict() for item in self.findings],
      "skippedAnalyses": [item.to_dict() for item in self.skipped_analyses],
    }


@dataclass(frozen=True)
class DiagnosisConfig:
  """The rules, incident, and output path for one regex diagnosis run.

  Raises:
    ValueError: if `match_timeout_seconds` is invalid.
  """

  source: Path
  name: str
  incident: ConversationIncident
  rules: tuple[RegexRule, ...]
  output_path: Path
  match_timeout_seconds: float = _DEFAULT_MATCH_TIMEOUT_SECONDS

  def __post_init__(self) -> None:
    _ = _validate_match_timeout(value=self.match_timeout_seconds)

  @classmethod
  def from_yaml(cls, path: str | Path) -> DiagnosisConfig:
    """
    Raises:
      TypeError: if the document has the wrong shape.
      ValueError: if `rules` is empty, rule ids collide, `output` is empty,
        or `match_timeout_seconds` is invalid.
    """
    source: Path = Path(path).resolve()
    loaded: object = cast(
      "object", yaml.safe_load(stream=source.read_text(encoding="utf-8"))
    )
    raw: dict[str, object] = _mapping(
      raw=loaded or {},
      name="diagnosis",
    )
    raw_rules_value: object | None = raw.get("rules")
    if not isinstance(raw_rules_value, list) or not raw_rules_value:
      raise ValueError("rules must be a non-empty list")
    raw_rules: list[object] = cast("list[object]", raw_rules_value)
    rules: tuple[RegexRule, ...] = tuple(
      RegexRule.from_dict(raw=item, index=index) for index, item in enumerate(raw_rules)
    )
    rule_ids: list[str] = [rule.id for rule in rules]
    duplicate_ids: list[str] = sorted(
      rule_id for rule_id in set(rule_ids) if rule_ids.count(rule_id) > 1
    )
    if duplicate_ids:
      raise ValueError(f"rule IDs must be unique: {duplicate_ids}")
    output = raw.get("output", "diagnosis.json")
    if not isinstance(output, str) or not output.strip():
      raise ValueError("output must be a non-empty path string")
    output_path: Path = Path(output)
    if not output_path.is_absolute():
      output_path = source.parent / output_path
    match_timeout_seconds: float = _validate_match_timeout(
      value=raw.get("match_timeout_seconds", _DEFAULT_MATCH_TIMEOUT_SECONDS)
    )
    return cls(
      source=source,
      name=_text(raw=raw.get("name"), name="name"),
      incident=ConversationIncident.from_dict(raw=raw.get("conversation")),
      rules=rules,
      output_path=output_path,
      match_timeout_seconds=match_timeout_seconds,
    )


class RegexDiagnoser:
  """Diagnose regex guardrails against one concrete conversation incident."""

  def diagnose(self, config: DiagnosisConfig) -> DiagnosisReport:
    """Diagnose a single incident. See `diagnose_many` for batches."""
    return self.diagnose_many(configs=(config,))[0]

  def diagnose_many(
    self,
    configs: tuple[DiagnosisConfig, ...],
  ) -> tuple[DiagnosisReport, ...]:
    """Diagnose multiple incidents against a single shared regex worker process."""
    with _RegexWorker() as worker:
      return tuple(self._diagnose_with_worker(config, worker) for config in configs)

  def _diagnose_with_worker(
    self,
    config: DiagnosisConfig,
    worker: _RegexWorker,
  ) -> DiagnosisReport:
    matches_by_rule: dict[str, tuple[RegexMatchEvidence, ...]] = {
      rule.id: self._evaluate(
        worker,
        config.incident,
        rule_id=rule.id,
        action=rule.action,
        pattern=rule.pattern,
        flags=rule.flags,
        timeout_seconds=config.match_timeout_seconds,
      )
      for rule in config.rules
    }
    interpretation_matches: InterpretationMatches = {
      rule.id: self._evaluate_interpretations(
        worker,
        config.incident,
        rule,
        timeout_seconds=config.match_timeout_seconds,
      )
      for rule in config.rules
      if rule.interpretations
    }
    matches: tuple[RegexMatchEvidence, ...] = tuple(
      evidence for rule in config.rules for evidence in matches_by_rule[rule.id]
    )
    symbolic_findings: list[Finding] = self._analyze_symbolically(
      config, matches_by_rule, interpretation_matches
    )
    proofs: dict[tuple[BugType, tuple[str, ...]], Finding] = {
      (finding.bug_type, finding.rule_ids): finding for finding in symbolic_findings
    }
    findings: list[DiagnosisFinding] = [
      *self._find_over_strict(config, matches_by_rule, proofs),
      *self._find_contradictions(config, matches_by_rule, proofs),
      *self._find_ambiguities(config, proofs, interpretation_matches),
    ]
    skipped: list[SkippedAnalysis] = []
    if (
      config.incident.expected_outcome is None
      or config.incident.expected_reason is None
    ):
      if config.incident.expected_outcome is None:
        requirement = "an expected outcome and policy reason are required"
      else:
        requirement = "a policy reason is required"
      skipped.append(
        SkippedAnalysis(
          bug_type="over_strict",
          reason=(
            f"For this agent output, {requirement} to establish "
            "that a blocking match is over-strict"
          ),
        )
      )
    if not any(rule.interpretations for rule in config.rules):
      skipped.append(
        SkippedAnalysis(
          bug_type="ambiguity",
          reason=(
            "regex execution is deterministic; two explicit policy "
            "interpretations are required to diagnose ambiguity"
          ),
        )
      )
    decisions: set[str] = {rule.action for rule in config.rules}
    if decisions != {"allow", "block"}:
      skipped.append(
        SkippedAnalysis(
          bug_type="contradiction",
          reason=(
            "contradiction analysis requires both allow and block "
            "rules; this rule collection does not contain both actions"
          ),
        )
      )
    return DiagnosisReport(
      name=config.name,
      incident=config.incident,
      matches=matches,
      findings=tuple(sorted(findings, key=lambda item: (item.bug_type, item.id))),
      skipped_analyses=tuple(skipped),
    )

  def _analyze_symbolically(
    self,
    config: DiagnosisConfig,
    matches_by_rule: dict[str, tuple[RegexMatchEvidence, ...]],
    interpretation_matches: InterpretationMatches,
  ) -> list[Finding]:
    predicates: dict[str, str] = {
      "incident_expected_allow": ("The supplied incident is expected to be allowed")
    }
    assignments: dict[str, bool] = {
      "incident_expected_allow": (config.incident.expected_outcome == "allow")
    }
    semantics: list[RuleSemantics] = []
    guardrail_rules: list[GuardrailRule] = []
    ambiguities: list[Ambiguity] = []
    for rule_index, rule in enumerate(config.rules, start=1):
      match_predicate: str = f"rule_{rule_index}_matches_incident"
      predicates[match_predicate] = (
        f"Regex rule {rule.id} matches the supplied incident location"
      )
      assignments[match_predicate] = bool(matches_by_rule[rule.id])
      semantics.append(
        RuleSemantics(
          rule_id=rule.id,
          decision=rule.action,
          condition=((Literal(predicate=match_predicate),),),
          rationale="Applicability is grounded by exact regex execution.",
        )
      )
      guardrail_rules.append(GuardrailRule(id=rule.id, text=rule.pattern))
      if not rule.interpretations:
        continue
      interpretations: list[AmbiguityInterpretation] = []
      for interpretation_index, (interpretation, evidence) in enumerate(
        zip(rule.interpretations, interpretation_matches[rule.id], strict=True),
        start=1,
      ):
        predicate: str = (
          f"rule_{rule_index}_interpretation_{interpretation_index}_matches_incident"
        )
        predicates[predicate] = (
          f"Interpretation {interpretation.label!r} of {rule.id} "
          "matches the supplied incident location"
        )
        assignments[predicate] = bool(evidence)
        interpretations.append(
          AmbiguityInterpretation(
            label=interpretation.label,
            decision=interpretation.action,
            condition=((Literal(predicate),),),
            rationale=("Applicability is grounded by exact regex execution."),
          )
        )
      ambiguities.append(
        Ambiguity(
          rule_id=rule.id,
          category="regex_policy_interpretation",
          excerpt=rule.description or rule.pattern,
          rationale=(
            "Two supplied policy interpretations may handle the incident differently."
          ),
          interpretation_a=interpretations[0],
          interpretation_b=interpretations[1],
        )
      )
    model: SymbolicModel = SymbolicModel(
      predicates=predicates,
      safe_condition=((Literal(predicate="incident_expected_allow"),),),
      rules=tuple(semantics),
      ambiguities=tuple(ambiguities),
    )
    guardrail: Guardrail = Guardrail(
      name=config.name,
      system_prompt="Diagnose exact regex guardrail matches.",
      rules=tuple(guardrail_rules),
    )
    return SymbolicAnalyzer(
      model,
      guardrail,
      predicate_assignments=assignments,
    ).analyze()

  def _evaluate(
    self,
    worker: _RegexWorker,
    incident: ConversationIncident,
    rule_id: str,
    action: Decision,
    pattern: str,
    flags: tuple[RegexFlagName, ...],
    timeout_seconds: float,
    interpretation: str | None = None,
  ) -> tuple[RegexMatchEvidence, ...]:
    raw_matches: tuple[RawRegexMatch, ...] = worker.evaluate(
      incident,
      rule_id,
      pattern,
      flags,
      timeout_seconds,
    )
    return tuple(
      RegexMatchEvidence(
        rule_id=rule_id,
        action=action,
        pattern=pattern,
        matched_text=matched_text,
        start=start,
        end=end,
        groups=groups,
        interpretation=interpretation,
      )
      for matched_text, start, end, groups in raw_matches
    )

  def _evaluate_interpretations(
    self,
    worker: _RegexWorker,
    incident: ConversationIncident,
    rule: RegexRule,
    timeout_seconds: float,
  ) -> tuple[tuple[RegexMatchEvidence, ...], tuple[RegexMatchEvidence, ...]]:
    left: RegexInterpretation = rule.interpretations[0]
    right: RegexInterpretation = rule.interpretations[1]
    return (
      self._evaluate(
        worker,
        incident,
        rule_id=rule.id,
        action=left.action,
        pattern=left.pattern,
        flags=rule.flags,
        timeout_seconds=timeout_seconds,
        interpretation=left.label,
      ),
      self._evaluate(
        worker,
        incident,
        rule_id=rule.id,
        action=right.action,
        pattern=right.pattern,
        flags=rule.flags,
        timeout_seconds=timeout_seconds,
        interpretation=right.label,
      ),
    )

  def _find_over_strict(
    self,
    config: DiagnosisConfig,
    matches_by_rule: dict[str, tuple[RegexMatchEvidence, ...]],
    proofs: dict[tuple[BugType, tuple[str, ...]], Finding],
  ) -> list[DiagnosisFinding]:
    if (
      config.incident.expected_outcome != "allow"
      or config.incident.expected_reason is None
    ):
      return []
    findings: list[DiagnosisFinding] = []
    for rule_index, rule in enumerate(config.rules, start=1):
      evidence: tuple[RegexMatchEvidence, ...] = matches_by_rule[rule.id]
      if rule.action != "block" or not evidence:
        continue
      symbolic_finding: Finding | None = proofs.get(("over_strict", (rule.id,)))
      if symbolic_finding is None:
        continue
      matched_values: str = self._matched_values(evidence)
      context: str = self._context_excerpt(config.incident, evidence)
      remediation: str = (
        f"Narrow {rule.id} or add an exception for the intended safe "
        "case, then retain this incident as an allow regression test."
      )
      expected_reason: str = config.incident.expected_reason
      findings.append(
        DiagnosisFinding(
          id=f"over-strict:{rule.id}",
          bug_type="over_strict",
          severity="high",
          rule_ids=(rule.id,),
          summary=f"{rule.id} blocks agent output expected to be allowed",
          explanation=(
            f"{rule.id} matched {self._locations(evidence)} and requires "
            "block, while the supplied policy label requires allow. "
            f"Reason: {expected_reason}"
          ),
          remediation=remediation,
          evidence=evidence,
          proof=SymbolicProof(
            formula=(f"incident_expected_allow AND rule_{rule_index}_matches_incident"),
            witness=symbolic_finding.witness,
            rationale=(
              "Z3 found the exact blocking regex match and the supplied "
              "expected-allow policy label simultaneously satisfiable. "
              "The label source is "
              f"{config.incident.expected_outcome_source}."
            ),
          ),
          counterexample_explanation=CounterexampleExplanation(
            what_matched=(
              f"The blocking regex {rule.id} matched {matched_values} "
              f"at {self._locations(evidence)}."
            ),
            counterexample=(
              f"In the supplied agent output, {context}, the policy "
              f"label expects allow because: {expected_reason}"
            ),
            why_violation=(
              "Z3 satisfied both facts at once: this blocking rule "
              "matches the agent output, and its supplied policy label "
              "expects allow. That counterexample proves the rule is "
              "over-strict for this output."
            ),
            recommended_fix=remediation,
          ),
          metadata={
            "expectedOutcome": "allow",
            "expectedReason": expected_reason,
            "expectedOutcomeSource": (config.incident.expected_outcome_source),
            "ruleAction": "block",
          },
        )
      )
    return findings

  def _find_contradictions(
    self,
    config: DiagnosisConfig,
    matches_by_rule: dict[str, tuple[RegexMatchEvidence, ...]],
    proofs: dict[tuple[BugType, tuple[str, ...]], Finding],
  ) -> list[DiagnosisFinding]:
    active_rules: list[RegexRule] = [
      rule for rule in config.rules if matches_by_rule[rule.id]
    ]
    findings: list[DiagnosisFinding] = []
    for left, right in combinations(active_rules, 2):
      if left.action == right.action:
        continue
      evidence: tuple[RegexMatchEvidence, ...] = self._overlapping_evidence(
        left=matches_by_rule[left.id],
        right=matches_by_rule[right.id],
      )
      if not evidence:
        continue
      ordered: tuple[str, ...] = tuple(sorted((left.id, right.id)))
      symbolic_finding: Finding | None = proofs.get(("contradiction", ordered))
      if symbolic_finding is None:
        continue
      context: str = self._context_excerpt(config.incident, evidence)
      remediation = (
        "Narrow one pattern so the matches no longer overlap, or "
        "define explicit precedence for this case and add it as a "
        "regression test."
      )
      findings.append(
        DiagnosisFinding(
          id=f"contradiction:{ordered[0]}:{ordered[1]}",
          bug_type="contradiction",
          severity="high",
          rule_ids=ordered,
          summary=f"{ordered[0]} and {ordered[1]} require opposite outcomes",
          explanation=(
            f"Both rules matched the incident at {self._locations(evidence)}, "
            f"but {left.id} requires {left.action} and {right.id} "
            f"requires {right.action}."
          ),
          remediation=remediation,
          evidence=evidence,
          proof=SymbolicProof(
            formula=(
              f"matches({left.id}) AND matches({right.id}) "
              f"AND ({left.action} != {right.action})"
            ),
            witness=symbolic_finding.witness,
            rationale=symbolic_finding.rationale,
          ),
          counterexample_explanation=CounterexampleExplanation(
            what_matched=(
              f"The same context, {context}, matched both {left.id} and {right.id}."
            ),
            counterexample=(
              f"{left.id} requires {left.action}, while {right.id} "
              f"requires {right.action} for that concrete text."
            ),
            why_violation=(
              "Z3 satisfied both rule-applicability predicates "
              "simultaneously. Because their required decisions "
              "are opposite, the conversation is a concrete "
              "counterexample to a consistent guardrail outcome."
            ),
            recommended_fix=remediation,
          ),
          metadata={left.id: left.action, right.id: right.action},
        )
      )
    return findings

  @staticmethod
  def _overlapping_evidence(
    left: tuple[RegexMatchEvidence, ...],
    right: tuple[RegexMatchEvidence, ...],
  ) -> tuple[RegexMatchEvidence, ...]:
    overlapping_left: tuple[RegexMatchEvidence, ...] = tuple(
      item
      for item in left
      if any(item.start < other.end and item.end > other.start for other in right)
    )
    overlapping_right: tuple[RegexMatchEvidence, ...] = tuple(
      item
      for item in right
      if any(item.start < other.end and item.end > other.start for other in left)
    )
    return (*overlapping_left, *overlapping_right)

  def _find_ambiguities(
    self,
    config: DiagnosisConfig,
    proofs: dict[tuple[BugType, tuple[str, ...]], Finding],
    interpretation_matches: InterpretationMatches,
  ) -> list[DiagnosisFinding]:
    findings: list[DiagnosisFinding] = []
    for rule in config.rules:
      if not rule.interpretations:
        continue
      left, right = rule.interpretations
      left_evidence, right_evidence = interpretation_matches[rule.id]
      left_outcome: Decision | None = left.action if left_evidence else None
      right_outcome: Decision | None = right.action if right_evidence else None
      if left_outcome == right_outcome:
        continue
      symbolic_finding: Finding | None = proofs.get(("ambiguity", (rule.id,)))
      if symbolic_finding is None:
        continue
      evidence: tuple[RegexMatchEvidence, ...] = (*left_evidence, *right_evidence)
      operator: TypingLiteral["XOR", "OR"] = (
        "XOR" if left.action == right.action else "OR"
      )
      context: str = self._context_excerpt(config.incident, evidence)
      remediation: str = (
        f"Choose the intended scope between {left.label!r} and "
        f"{right.label!r}, encode only that pattern, and retain this "
        "incident as a regression test."
      )
      findings.append(
        DiagnosisFinding(
          id=f"ambiguity:{rule.id}",
          bug_type="ambiguity",
          severity="medium",
          rule_ids=(rule.id,),
          summary=f"{rule.id}'s interpretations diverge for this conversation",
          explanation=(
            f"Interpretation {left.label!r} produces "
            f"'{left_outcome or 'no decision'}', while {right.label!r} "
            f"produces '{right_outcome or 'no decision'}'."
          ),
          remediation=remediation,
          evidence=evidence,
          proof=SymbolicProof(
            formula=(f"matches({left.label}) {operator} matches({right.label})"),
            witness=symbolic_finding.witness,
            rationale=symbolic_finding.rationale,
          ),
          counterexample_explanation=CounterexampleExplanation(
            what_matched=(
              f"For {context}, interpretation {left.label!r} produces "
              f"'{left_outcome or 'no decision'}', while "
              f"{right.label!r} produces "
              f"'{right_outcome or 'no decision'}'."
            ),
            counterexample=(
              "The supplied conversation makes the two plausible "
              "policy readings behave differently."
            ),
            why_violation=(
              f"Z3 proved the interpretations diverge using {operator}. "
              "The conversation is therefore a concrete counterexample "
              "to the rule having one unambiguous outcome."
            ),
            recommended_fix=remediation,
          ),
          metadata={
            "interpretationA": left.label,
            "interpretationAOutcome": left_outcome or "not_applicable",
            "interpretationB": right.label,
            "interpretationBOutcome": right_outcome or "not_applicable",
          },
        )
      )
    return findings

  @staticmethod
  def _locations(evidence: tuple[RegexMatchEvidence, ...]) -> str:
    return ", ".join(f"[{item.start}, {item.end})" for item in evidence)

  @staticmethod
  def _matched_values(evidence: tuple[RegexMatchEvidence, ...]) -> str:
    values: list[str] = sorted({item.matched_text for item in evidence})
    return ", ".join(repr(value) for value in values)

  @staticmethod
  def _context_excerpt(
    incident: ConversationIncident,
    evidence: tuple[RegexMatchEvidence, ...],
    surrounding_characters: int = 80,
  ) -> str:
    start: int = max(0, min(item.start for item in evidence) - surrounding_characters)
    end: int = min(
      len(incident.text),
      max(item.end for item in evidence) + surrounding_characters,
    )
    excerpt: str = incident.text[start:end]
    if start > 0:
      excerpt = f"...{excerpt}"
    if end < len(incident.text):
      excerpt = f"{excerpt}..."
    return repr(excerpt)
