"""Domain model for guardrails, extracted symbolic rules, and analysis findings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal as TypingLiteral
from typing import cast

type Decision = TypingLiteral["allow", "block"]
type BugType = TypingLiteral["over_strict", "contradiction", "ambiguity"]


def _require_text(value: object, name: str) -> str:
  if not isinstance(value, str) or not value.strip():
    raise ValueError(f"{name} must be a non-empty string")
  return value.strip()


def _require_literal[T](value: object, options: tuple[T, ...], message: str) -> T:
  for option in options:
    if value == option:
      return option
  raise ValueError(message)


def _mapping(raw: object, message: str) -> dict[str, object]:
  if not isinstance(raw, dict):
    raise TypeError(message)
  return cast("dict[str, object]", raw)


def _list_of(raw: object, message: str) -> list[object]:
  if not isinstance(raw, list):
    raise TypeError(message)
  return cast("list[object]", raw)


def _as_list(raw: object) -> list[object] | None:
  return cast("list[object]", raw) if isinstance(raw, list) else None


@dataclass(frozen=True)
class GuardrailRule:
  """A single natural-language guardrail rule identified by a stable id.

  Raises:
    ValueError: if `id` or `text` is empty.
  """

  id: str
  text: str

  def __post_init__(self) -> None:
    _ = _require_text(self.id, name="rule.id")
    _ = _require_text(self.text, name="rule.text")


@dataclass(frozen=True)
class GuardrailExample:
  """A labeled example conversation turn used to ground semantic extraction."""

  content: str
  should_block: bool
  reasoning: str = ""


@dataclass(frozen=True)
class Guardrail:
  """A natural-language guardrail: system prompt, numbered rules, and examples."""

  name: str
  system_prompt: str
  rules: tuple[GuardrailRule, ...]
  examples: tuple[GuardrailExample, ...] = ()


@dataclass(frozen=True)
class Literal:
  """A single predicate assignment, e.g. `is_educational = true`."""

  predicate: str
  value: bool = True

  @classmethod
  def from_dict(cls, raw: object) -> Literal:
    """
    Raises:
      TypeError: if `raw` is not a mapping, or `value` is not a boolean.
      ValueError: if `predicate` is missing or empty.
    """
    mapping = _mapping(raw, message="condition literals must be mappings")
    predicate: str = _require_text(mapping.get("predicate"), name="literal.predicate")
    value = mapping.get("value", True)
    if not isinstance(value, bool):
      raise TypeError("literal.value must be a boolean")
    return cls(predicate=predicate, value=value)

  def to_dict(self) -> dict[str, object]:
    return {"predicate": self.predicate, "value": self.value}


type Clause = tuple[Literal, ...]
type Condition = tuple[Clause, ...]


def parse_condition(raw: object, name: str) -> Condition:
  """Parse a condition in disjunctive normal form: an OR of AND-clauses.

  Raises:
    TypeError: if `raw`, or any clause within it, is not a list.
    ValueError: if `raw`, or any clause within it, is empty.
  """
  raw_clauses = _list_of(raw, message=f"{name} must contain at least one AND-clause")
  if not raw_clauses:
    raise ValueError(f"{name} must contain at least one AND-clause")
  clauses: list[Clause] = []
  for index, raw_clause in enumerate(raw_clauses):
    clause = _list_of(
      raw_clause, message=f"{name}[{index}] must contain at least one literal"
    )
    if not clause:
      raise ValueError(f"{name}[{index}] must contain at least one literal")
    clauses.append(tuple(Literal.from_dict(raw_item) for raw_item in clause))
  return tuple(clauses)


def condition_to_list(condition: Condition) -> list[list[dict[str, object]]]:
  """Serialize a `Condition` back to the nested-list DNF shape used in YAML/JSON."""
  return [[literal.to_dict() for literal in clause] for clause in condition]


@dataclass(frozen=True)
class RuleSemantics:
  """The symbolic decision, DNF condition, and rationale for one guardrail rule."""

  rule_id: str
  decision: Decision
  condition: Condition
  rationale: str = ""

  @classmethod
  def from_dict(cls, raw: object) -> RuleSemantics:
    """
    Raises:
      TypeError: if `raw` is not a mapping, or `condition` is malformed.
      ValueError: if `rule_id` is empty or `decision` is not 'allow'/'block'.
    """
    mapping = _mapping(raw, message="symbolic rules must be mappings")
    decision: Decision = _require_literal(
      mapping.get("decision"),
      ("allow", "block"),
      message="rule decision must be 'allow' or 'block'",
    )
    return cls(
      rule_id=_require_text(mapping.get("rule_id"), name="rule.rule_id"),
      decision=decision,
      condition=parse_condition(mapping.get("condition"), name="rule.condition"),
      rationale=str(mapping.get("rationale", "")).strip(),
    )

  def to_dict(self) -> dict[str, object]:
    return {
      "rule_id": self.rule_id,
      "decision": self.decision,
      "condition": condition_to_list(self.condition),
      "rationale": self.rationale,
    }


@dataclass(frozen=True)
class AmbiguityInterpretation:
  """One materially distinct, formalized reading of an ambiguous rule."""

  label: str
  decision: Decision
  condition: Condition
  rationale: str

  @classmethod
  def from_dict(cls, raw: object, name: str) -> AmbiguityInterpretation:
    """
    Raises:
      TypeError: if `raw` is not a mapping, or `condition` is malformed.
      ValueError: if `label` or `rationale` is empty, or `decision` is invalid.
    """
    mapping = _mapping(raw, message=f"{name} must be a mapping")
    decision: Decision = _require_literal(
      mapping.get("decision"),
      ("allow", "block"),
      message=f"{name}.decision must be 'allow' or 'block'",
    )
    return cls(
      label=_require_text(mapping.get("label"), name=f"{name}.label"),
      decision=decision,
      condition=parse_condition(mapping.get("condition"), name=f"{name}.condition"),
      rationale=_require_text(mapping.get("rationale"), name=f"{name}.rationale"),
    )

  def to_dict(self) -> dict[str, object]:
    return {
      "label": self.label,
      "decision": self.decision,
      "condition": condition_to_list(self.condition),
      "rationale": self.rationale,
    }


@dataclass(frozen=True)
class Ambiguity:
  """A rule flagged as ambiguous, with two competing formalized interpretations."""

  rule_id: str
  category: str
  excerpt: str
  rationale: str
  interpretation_a: AmbiguityInterpretation
  interpretation_b: AmbiguityInterpretation
  severity: TypingLiteral["low", "medium", "high"] = "medium"

  @classmethod
  def from_dict(cls, raw: object) -> Ambiguity:
    """
    Raises:
      TypeError: if `raw` is not a mapping, or an interpretation is malformed.
      ValueError: if a required text field is empty or `severity` is invalid.
    """
    mapping = _mapping(raw, message="ambiguities must be mappings")
    severity: TypingLiteral["low", "medium", "high"] = _require_literal(
      mapping.get("severity", "medium"),
      ("low", "medium", "high"),
      message="ambiguity severity must be low, medium, or high",
    )
    return cls(
      rule_id=_require_text(mapping.get("rule_id"), name="ambiguity.rule_id"),
      category=_require_text(mapping.get("category"), name="ambiguity.category"),
      excerpt=_require_text(mapping.get("excerpt"), name="ambiguity.excerpt"),
      rationale=_require_text(mapping.get("rationale"), name="ambiguity.rationale"),
      interpretation_a=AmbiguityInterpretation.from_dict(
        mapping.get("interpretation_a"), name="ambiguity.interpretation_a"
      ),
      interpretation_b=AmbiguityInterpretation.from_dict(
        mapping.get("interpretation_b"), name="ambiguity.interpretation_b"
      ),
      severity=severity,
    )

  def to_dict(self) -> dict[str, object]:
    return {
      "rule_id": self.rule_id,
      "category": self.category,
      "excerpt": self.excerpt,
      "rationale": self.rationale,
      "interpretation_a": self.interpretation_a.to_dict(),
      "interpretation_b": self.interpretation_b.to_dict(),
      "severity": self.severity,
    }


@dataclass(frozen=True)
class SymbolicModel:
  """The boolean model extracted from a guardrail: predicates, rules, and ambiguities."""

  predicates: dict[str, str]
  safe_condition: Condition
  rules: tuple[RuleSemantics, ...]
  exclusions: tuple[tuple[str, ...], ...] = ()
  ambiguities: tuple[Ambiguity, ...] = ()

  @classmethod
  def from_dict(cls, raw: object) -> SymbolicModel:
    """
    Raises:
      TypeError: if `raw`, `predicates`, `rules`, or `exclusions` has the wrong shape.
      ValueError: if `predicates` is empty or a condition references an unknown
        predicate (see `validate_references`).
    """
    mapping = _mapping(raw, message="symbolic model must be a mapping")
    raw_predicates = mapping.get("predicates")
    if not isinstance(raw_predicates, dict) or not raw_predicates:
      raise ValueError("symbolic model predicates must be a non-empty mapping")
    predicates: dict[str, str] = {
      _require_text(key, name="predicate name"): _require_text(
        value, name="predicate description"
      )
      for key, value in cast("dict[object, object]", raw_predicates).items()
    }
    exclusions_raw = _list_of(
      mapping.get("exclusions", []),
      message="symbolic model exclusions must be a list",
    )
    exclusions: tuple[tuple[str, ...], ...] = tuple(
      tuple(_require_text(item, name="exclusion predicate") for item in group)
      for raw_group in exclusions_raw
      for group in [_as_list(raw_group)]
      if group is not None and len(group) > 1
    )
    model = cls(
      predicates=predicates,
      safe_condition=parse_condition(
        mapping.get("safe_condition"), name="safe_condition"
      ),
      rules=tuple(
        RuleSemantics.from_dict(raw_item)
        for raw_item in _list_of(
          mapping.get("rules", []), message="symbolic model rules must be a list"
        )
      ),
      exclusions=exclusions,
      ambiguities=tuple(
        Ambiguity.from_dict(raw_item)
        for raw_item in _list_of(
          mapping.get("ambiguities", []),
          message="symbolic model ambiguities must be a list",
        )
      ),
    )
    model.validate_references()
    return model

  def validate_references(self) -> None:
    """
    Raises:
      ValueError: if any condition or exclusion references a predicate not
        declared in `predicates`.
    """
    known: set[str] = set(self.predicates)
    referenced: set[str] = {
      literal.predicate
      for condition in (
        self.safe_condition,
        *[rule.condition for rule in self.rules],
        *[
          interpretation.condition
          for ambiguity in self.ambiguities
          for interpretation in (
            ambiguity.interpretation_a,
            ambiguity.interpretation_b,
          )
        ],
      )
      for clause in condition
      for literal in clause
    }
    referenced.update(item for group in self.exclusions for item in group)
    unknown: set[str] = referenced - known
    if unknown:
      raise ValueError(
        f"symbolic model references unknown predicates: {sorted(unknown)}"
      )

  def to_dict(self) -> dict[str, object]:
    return {
      "predicates": self.predicates,
      "safe_condition": condition_to_list(self.safe_condition),
      "rules": [rule.to_dict() for rule in self.rules],
      "exclusions": [list(group) for group in self.exclusions],
      "ambiguities": [ambiguity.to_dict() for ambiguity in self.ambiguities],
    }


@dataclass(frozen=True)
class Finding:
  """A single proven defect (over-strict rule, contradiction, or ambiguity).

  `witness` holds the satisfying predicate assignment Z3 found as evidence.
  """

  id: str
  bug_type: BugType
  severity: str
  rule_ids: tuple[str, ...]
  summary: str
  rationale: str
  witness: dict[str, bool] = field(default_factory=dict)
  metadata: dict[str, object] = field(default_factory=dict)

  def to_dict(self) -> dict[str, object]:
    return {
      "id": self.id,
      "type": self.bug_type,
      "severity": self.severity,
      "ruleIds": list(self.rule_ids),
      "summary": self.summary,
      "rationale": self.rationale,
      "witness": dict(sorted(self.witness.items())),
      "metadata": dict(self.metadata),
    }
