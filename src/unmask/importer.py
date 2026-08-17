"""Import stakeholder regex and violation artifacts into diagnosis inputs."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import yaml

from .diagnosis import RegexManifest, RegexRule

if TYPE_CHECKING:
  from .models import Decision

_QUOTED_STRING = r"""(?:'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")"""


@dataclass(frozen=True)
class ImportedRegex:
  """A named regex constant statically extracted from a Python source file."""

  name: str
  pattern: str

  @property
  def rule_id(self) -> str:
    return self.name.removeprefix("_").lower().replace("_", "-")


@dataclass(frozen=True)
class ImportedViolation:
  """One observed guardrail violation: the matched value and its context."""

  context: str
  source_location: int
  value: str
  expected_outcome: Decision | None = None
  expected_reason: str | None = None

  @property
  def local_start(self) -> int:
    """
    Raises:
      ValueError: if `value` does not occur exactly once in `context`.
    """
    start: int = self.context.find(self.value)
    if start < 0:
      raise ValueError("violation value does not occur in violation context")
    next_start: int = start + max(len(self.value), 1)
    if self.context.find(self.value, next_start) >= 0:
      raise ValueError(
        "violation value occurs more than once in context; full conversation "
        + "or an unambiguous context window is required"
      )
    return start


def _expected_label(
  outcome: object,
  reason: object,
  name: str,
) -> tuple[Decision | None, str | None]:
  """
  Raises:
    ValueError: if `outcome` is not 'allow'/'block', `reason` is empty, or
      exactly one of `outcome`/`reason` is provided.
  """
  if outcome is not None and outcome not in ("allow", "block"):
    raise ValueError(f"{name}.expected_outcome must be 'allow' or 'block'")
  if reason is not None and (not isinstance(reason, str) or not reason.strip()):
    raise ValueError(f"{name}.expected_reason must be a non-empty string")
  if (outcome is None) != (reason is None):
    raise ValueError(
      f"{name}.expected_outcome and expected_reason must be provided together"
    )
  return (
    outcome,
    reason.strip() if isinstance(reason, str) else None,
  )


class PythonRegexImporter:
  """Statically expand a Python regex collection without executing it."""

  def load(self, path: str | Path) -> tuple[ImportedRegex, ...]:
    """Parse a `PROHIBITED_PATTERNS = _compile_patterns(...)` module without executing it.

    Raises:
      ValueError: if the source has a syntax error, `PROHIBITED_PATTERNS` is
        missing, empty, or not a direct `_compile_patterns(...)` call, or a
        referenced string constant cannot be statically resolved.
      TypeError: if a pattern argument or string expression has an
        unsupported shape.
    """
    source: Path = Path(path)
    try:
      module: ast.Module = ast.parse(
        source.read_text(encoding="utf-8"), filename=str(source)
      )
    except SyntaxError as exc:
      raise ValueError(f"invalid Python regex source: {exc}") from exc

    strings: dict[str, str] = {}
    collection: tuple[ImportedRegex, ...] | None = None
    for statement in module.body:
      assignment: tuple[str, ast.expr] | None = self._assignment(statement)
      if assignment is None:
        continue
      name, expression = assignment
      if name == "PROHIBITED_PATTERNS":
        collection = self._collection(expression, strings)
        continue
      try:
        strings[name] = self._string(expression, strings)
      except TypeError, ValueError:
        continue

    if collection is None:
      raise ValueError(
        "regex source must define PROHIBITED_PATTERNS using '_compile_patterns(...)'"
      )
    if not collection:
      raise ValueError("PROHIBITED_PATTERNS must contain at least one pattern")
    return collection

  @staticmethod
  def _assignment(
    statement: ast.stmt,
  ) -> tuple[str, ast.expr] | None:
    if (
      isinstance(statement, ast.Assign)
      and len(statement.targets) == 1
      and isinstance(statement.targets[0], ast.Name)
    ):
      return statement.targets[0].id, statement.value
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
      if statement.value is None:
        return None
      return statement.target.id, statement.value
    return None

  def _collection(
    self, expression: ast.expr, strings: dict[str, str]
  ) -> tuple[ImportedRegex, ...]:
    if (
      not isinstance(expression, ast.Call)
      or not isinstance(expression.func, ast.Name)
      or expression.func.id != "_compile_patterns"
      or expression.keywords
    ):
      raise ValueError(
        "PROHIBITED_PATTERNS must be a direct _compile_patterns(...) call"
      )
    result: list[ImportedRegex] = []
    for argument in expression.args:
      if not isinstance(argument, ast.Name):
        raise TypeError("PROHIBITED_PATTERNS arguments must be named regex constants")
      if argument.id not in strings:
        raise ValueError(f"PROHIBITED_PATTERNS references unresolved {argument.id}")
      result.append(ImportedRegex(name=argument.id, pattern=strings[argument.id]))
    return tuple(result)

  def _string(self, expression: ast.expr, strings: dict[str, str]) -> str:
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
      return expression.value
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
      return self._string(expression.left, strings) + self._string(
        expression.right, strings
      )
    if isinstance(expression, ast.Name):
      try:
        return strings[expression.id]
      except KeyError as exc:
        raise ValueError(f"unresolved string constant {expression.id}") from exc
    if isinstance(expression, ast.JoinedStr):
      parts: list[str] = []
      for value in expression.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
          parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
          if value.conversion != -1 or value.format_spec is not None:
            raise ValueError("formatted regex values cannot use conversions")
          parts.append(self._string(expression=value.value, strings=strings))
        else:
          raise TypeError("unsupported formatted regex value")
      return "".join(parts)
    raise TypeError(f"unsupported regex expression: {type(expression).__name__}")


def load_violations(path: str | Path) -> tuple[ImportedViolation, ...]:
  """Load violations from a JSON file, falling back to a plain-text field format.

  Raises:
    ValueError: if the file matches neither the JSON nor the text violation shape.
    TypeError: if the parsed JSON is not an object, or an entry is not an object.
  """
  text: str = Path(path).read_text(encoding="utf-8")
  try:
    raw: object = cast("object", json.loads(text))
  except json.JSONDecodeError:
    return _load_text_violations(text)
  return _load_json_violations(raw)


def _load_json_violations(raw: object) -> tuple[ImportedViolation, ...]:
  if not isinstance(raw, dict):
    raise TypeError("JSON violation file must contain an object")
  data: dict[str, object] = cast("dict[str, object]", raw)
  raw_violations_value: object | None = data.get("violations")
  if not isinstance(raw_violations_value, list) or not raw_violations_value:
    raise ValueError("JSON violation file must contain a non-empty violations list")
  raw_violations: list[object] = cast("list[object]", raw_violations_value)
  violations: list[ImportedViolation] = []
  for index, item in enumerate(raw_violations):
    name: str = f"violations[{index}]"
    if not isinstance(item, dict):
      raise TypeError(f"{name} must be an object")
    violations.append(
      _load_json_violation(raw=cast("dict[str, object]", item), name=name)
    )
  return tuple(violations)


def _load_json_violation(raw: dict[str, object], name: str) -> ImportedViolation:
  context: object | None = raw.get("context")
  value: object | None = raw.get("value")
  location: object | None = raw.get("location")
  expected_outcome, expected_reason = _expected_label(
    outcome=raw.get("expected_outcome"),
    reason=raw.get("expected_reason"),
    name=name,
  )
  if not isinstance(context, str) or not context:
    raise ValueError(f"{name}.context must be a non-empty string")
  if not isinstance(value, str) or not value:
    raise ValueError(f"{name}.value must be a non-empty string")
  if not isinstance(location, int) or isinstance(location, bool) or location < 0:
    raise ValueError(f"{name}.location must be a non-negative integer")
  violation: ImportedViolation = ImportedViolation(
    context=context,
    source_location=location,
    value=value,
    expected_outcome=expected_outcome,
    expected_reason=expected_reason,
  )
  _ = violation.local_start
  return violation


def _load_text_violations(text: str) -> tuple[ImportedViolation, ...]:
  field_pattern: re.Pattern[str] = re.compile(
    rf"context:\s*(?P<context>{_QUOTED_STRING})\s*"
    + r"location:\s*(?P<location>\d+)\s*"
    + rf"value:\s*(?P<value>{_QUOTED_STRING})",
    re.DOTALL,
  )
  violations: tuple[ImportedViolation, ...] = tuple(
    ImportedViolation(
      context=_literal_string(match.group("context"), name="context"),
      source_location=int(match.group("location")),
      value=_literal_string(match.group("value"), name="value"),
    )
    for match in field_pattern.finditer(text)
  )
  if not violations:
    raise ValueError("violation file must contain context, location, and value fields")
  return violations


def _literal_string(value: str, name: str) -> str:
  parsed: object = cast("object", ast.literal_eval(node_or_string=value))
  if not isinstance(parsed, str) or not parsed:
    raise ValueError(f"violation {name} must be a non-empty string")
  return parsed


def build_diagnosis_input(
  *,
  name: str,
  regexes: tuple[ImportedRegex, ...],
  violation: ImportedViolation,
  expected_outcome: Decision | None = None,
  expected_reason: str | None = None,
  report_path: str = "output/diagnosis.json",
) -> dict[str, object]:
  """Build a `DiagnosisConfig`-shaped dict from imported regexes and one violation."""
  start: int = violation.local_start
  conversation: dict[str, object] = {
    "text": violation.context,
    "value": violation.value,
    "location": {"start": start, "end": start + len(violation.value)},
    "source_location": violation.source_location,
    "observed_outcome": "block",
  }
  expected_outcome, expected_reason = _expected_label(
    outcome=expected_outcome,
    reason=expected_reason,
    name="default",
  )
  label_outcome: Decision | None = violation.expected_outcome or expected_outcome
  label_reason: str | None = violation.expected_reason or expected_reason
  if label_outcome is not None and label_reason is not None:
    conversation.update(
      {
        "expected_outcome": label_outcome,
        "expected_reason": label_reason,
        "expected_outcome_source": (
          "violation" if violation.expected_outcome is not None else "cli_default"
        ),
      }
    )
  return {
    "name": name,
    "conversation": conversation,
    "rules": [
      {
        "id": regex.rule_id,
        "pattern": regex.pattern,
        "action": "block",
        "flags": ["IGNORECASE"],
        "description": f"Imported from {regex.name}.",
      }
      for regex in regexes
    ],
    "output": report_path,
  }


def imported_regex_manifest(
  name: str,
  regexes: tuple[ImportedRegex, ...],
) -> RegexManifest:
  """Wrap statically imported regexes as a blocking-only `RegexManifest`."""
  return RegexManifest(
    name=name,
    rules=tuple(
      RegexRule(
        id=regex.rule_id,
        pattern=regex.pattern,
        action="block",
        flags=("IGNORECASE",),
        description=f"Imported from Python constant {regex.name}.",
      )
      for regex in regexes
    ),
  )


def build_manifest_diagnosis_input(
  *,
  manifest: RegexManifest,
  violation: ImportedViolation,
  expected_outcome: Decision | None = None,
  expected_reason: str | None = None,
  report_path: str = "output/diagnosis.json",
) -> dict[str, object]:
  """Build a `DiagnosisConfig`-shaped dict from a full regex manifest and one violation."""
  data: dict[str, object] = build_diagnosis_input(
    name=manifest.name,
    regexes=(),
    violation=violation,
    expected_outcome=expected_outcome,
    expected_reason=expected_reason,
    report_path=report_path,
  )
  data["rules"] = [rule.to_dict() for rule in manifest.rules]
  data["match_timeout_seconds"] = manifest.match_timeout_seconds
  return data


def write_regex_manifest(path: str | Path, manifest: RegexManifest) -> Path:
  """Serialize `manifest` to YAML at `path`, creating parent directories as needed."""
  output: Path = Path(path)
  output.parent.mkdir(parents=True, exist_ok=True)
  _ = output.write_text(
    data=yaml.safe_dump(manifest.to_dict(), sort_keys=False),
    encoding="utf-8",
  )
  return output


def write_diagnosis_input(path: str | Path, data: dict[str, object]) -> Path:
  """Serialize `data` to YAML at `path`, creating parent directories as needed."""
  output: Path = Path(path)
  output.parent.mkdir(parents=True, exist_ok=True)
  _ = output.write_text(
    data=yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
    encoding="utf-8",
  )
  return output
