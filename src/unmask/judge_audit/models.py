"""Strict, bounded JSON IR. No executable model output."""

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

MAX_BYTES = 1000000
MAX_ATOMS = 128
MAX_NODES = 1024
MAX_DEPTH = 16


class ValidationError(ValueError):
  """A replay fails structural, resource or evidence validation."""


class Truth(StrEnum):
  """Known findings and explicitly unconstrained semantic facts."""

  TRUE = "TRUE"
  FALSE = "FALSE"
  UNKNOWN = "UNKNOWN"


def require(ok: bool, message: str) -> None:
  if not ok:
    raise ValidationError(message)


def validate_object(
  value: Any, required: set[str], optional: set[str] | frozenset[str] = frozenset()
) -> None:
  require(isinstance(value, dict), "expected JSON object")
  require(required <= value.keys(), f"missing fields: {required - value.keys()}")
  require(
    value.keys() <= required | optional,
    f"unexpected fields: {value.keys() - required - optional}",
  )


def require_text(value: Any) -> None:
  require(isinstance(value, str) and bool(value.strip()), "expected nonempty string")


def validate_items(value: Any, limit: int = MAX_ATOMS) -> None:
  require(
    isinstance(value, list) and len(value) <= limit,
    f"expected list with at most {limit} items",
  )


@dataclass(frozen=True)
class Expr:
  """A bounded Boolean expression tree."""

  op: str
  args: tuple[Expr, ...] = ()
  atom: str | None = None
  value: bool | None = None

  def referenced_atoms(self) -> set[str]:
    if self.atom is not None:
      return {self.atom}
    return set().union(*(child.referenced_atoms() for child in self.args))

  @classmethod
  def parse(
    cls, value: Any, atoms: set[str], budget: list[int], depth: int = 0
  ) -> Expr:
    budget[0] += 1
    require(
      budget[0] <= MAX_NODES and depth <= MAX_DEPTH,
      "expression resource limit exceeded",
    )
    require(isinstance(value, dict), "expression must be object")
    if "atom" in value:
      validate_object(value, {"atom"})
      require(
        isinstance(value["atom"], str) and value["atom"] in atoms, "undeclared atom"
      )
      return cls("atom", atom=value["atom"])
    if "const" in value:
      validate_object(value, {"const"})
      require(type(value["const"]) is bool, "const must be Boolean")
      return cls("const", value=value["const"])
    validate_object(value, {"op", "args"})
    op = value["op"]
    require(
      isinstance(op, str) and op in {"and", "or", "not", "implies", "iff"},
      "unsupported operator",
    )
    args = value["args"]
    validate_items(args)
    require(
      op in {"and", "or"}
      and len(args) >= 1
      or (op == "not" and len(args) == 1)
      or (op in {"implies", "iff"} and len(args) == 2),
      "invalid operator arity",
    )
    return cls(
      op,
      tuple(cls.parse(atom_entry, atoms, budget, depth + 1) for atom_entry in args),
    )


@dataclass(frozen=True)
class Atom:
  """A declared semantic or procedural proposition."""

  id: str
  role: str
  description: str


@dataclass(frozen=True)
class Evidence:
  """An exact half-open quotation in a replay source."""

  source: str
  start: int
  end: int
  quote: str


@dataclass(frozen=True)
class Finding:
  """A supplied annotation and its observable evidence."""

  atom: str
  value: Truth
  evidence: tuple[Evidence, ...]
  explanation: str


@dataclass(frozen=True)
class Rule:
  """A protocol constraint or obligation with optional coverage."""

  id: str
  description: str
  expr: Expr
  addressed: Expr | None = None


@dataclass(frozen=True)
class AuditInput:
  """A replay separating protocol, judge observations, grounding and gold."""

  id: str
  context: str
  response: str
  rationale: str
  verdict: str
  gold_verdict: str | None
  protocol_text: str
  atoms: tuple[Atom, ...]
  decision: Expr
  constraints: tuple[Rule, ...]
  requirements: tuple[Rule, ...]
  findings: tuple[Finding, ...]
  grounding_pairs: tuple[tuple[str, str], ...]
  extraction: dict[str, str]

  @classmethod
  def from_json(cls, text: str) -> AuditInput:
    """Parse replay JSON with bounded resources and unique keys.

    Raises:
      ValidationError: if JSON, resource bounds or evidence are invalid.
    """
    require(len(text.encode()) <= MAX_BYTES, "input exceeds byte limit")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
      result = {}
      for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
      return result

    def reject_constant(value: str) -> None:
      raise ValidationError(f"invalid JSON constant: {value}")

    try:
      return cls.parse(
        json.loads(text, object_pairs_hook=unique, parse_constant=reject_constant)
      )
    except (json.JSONDecodeError, RecursionError) as exc:
      raise ValidationError(f"invalid JSON: {exc}") from exc

  @classmethod
  def parse(cls, data: Any) -> AuditInput:
    """Validate a decoded replay without interpreting natural language.

    Raises:
      ValidationError: if its structure or references are invalid.
    """
    validate_object(
      data,
      {
        "schema_version",
        "id",
        "context",
        "response",
        "rationale",
        "verdict",
        "protocol",
        "findings",
        "extraction",
      },
      {"gold_verdict", "grounding_pairs"},
    )
    require(
      type(data["schema_version"]) is int and data["schema_version"] == 1,
      "unsupported schema version",
    )
    for key in ("id", "context", "response", "rationale"):
      require_text(data[key])
    require(data["verdict"] in ("PASS", "FAIL"), "verdict must be PASS or FAIL")
    require(data.get("gold_verdict") in (None, "PASS", "FAIL"), "invalid gold verdict")
    protocol = data["protocol"]
    validate_object(
      protocol, {"text", "atoms", "decision", "constraints", "requirements"}
    )
    require_text(protocol["text"])
    validate_items(protocol["atoms"])
    require(bool(protocol["atoms"]), "declare at least one atom")
    atoms = []
    for atom_entry in protocol["atoms"]:
      validate_object(atom_entry, {"id", "role", "description"})
      require(
        isinstance(atom_entry["id"], str)
        and re.fullmatch("[A-Za-z][A-Za-z0-9_]{0,63}", atom_entry["id"]) is not None,
        "invalid atom id",
      )
      require(
        atom_entry["role"] in ("judge", "context", "procedure"), "invalid atom role"
      )
      require_text(atom_entry["description"])
      atoms.append(Atom(**atom_entry))
    names = {atom_entry.id for atom_entry in atoms}
    require(len(names) == len(atoms), "duplicate atom id")
    roles = {atom_entry.id: atom_entry.role for atom_entry in atoms}
    budget = [0]
    decision = Expr.parse(protocol["decision"], names, budget)

    require(
      all(roles[name] != "context" for name in decision.referenced_atoms()),
      "decision may use judge/procedure findings, not independent context assessments",
    )
    seen_rules: set[str] = set()

    def parse_rules(key: str) -> tuple[Rule, ...]:
      validate_items(protocol[key])
      result = []
      for rule_entry in protocol[key]:
        validate_object(
          rule_entry,
          {"id", "description", "expr"},
          {"addressed"} if key == "requirements" else set(),
        )
        require_text(rule_entry["id"])
        require_text(rule_entry["description"])
        require(rule_entry["id"] not in seen_rules, "duplicate rule id")
        seen_rules.add(rule_entry["id"])
        expression = Expr.parse(rule_entry["expr"], names, budget)
        if key == "constraints":
          require(
            all(roles[name] != "context" for name in expression.referenced_atoms()),
            "protocol constraints cannot bind independent context assessments",
          )
        addressed = (
          Expr.parse(rule_entry["addressed"], names, budget)
          if "addressed" in rule_entry
          else None
        )
        result.append(
          Rule(rule_entry["id"], rule_entry["description"], expression, addressed)
        )
      return tuple(result)

    constraints, requirements = (
      parse_rules("constraints"),
      parse_rules("requirements"),
    )
    findings = []
    validate_items(data["findings"], MAX_ATOMS * 2)
    sources = {
      "context": data["context"],
      "response": data["response"],
      "rationale": data["rationale"],
      "protocol": protocol["text"],
    }
    for finding_entry in data["findings"]:
      validate_object(finding_entry, {"atom", "value", "evidence", "explanation"})
      require(
        isinstance(finding_entry["atom"], str) and finding_entry["atom"] in names,
        "finding refers to undeclared atom",
      )
      require(
        finding_entry["value"] in ("TRUE", "FALSE", "UNKNOWN"), "invalid truth value"
      )
      require_text(finding_entry["explanation"])
      validate_items(finding_entry["evidence"], 16)
      evidence = []
      for evidence_data in finding_entry["evidence"]:
        validate_object(evidence_data, {"source", "start", "end", "quote"})
        require(
          isinstance(evidence_data["source"], str)
          and evidence_data["source"] in sources,
          "invalid evidence source",
        )
        require(
          type(evidence_data["start"]) is int and type(evidence_data["end"]) is int,
          "evidence offsets must be integers",
        )
        require(
          0
          <= evidence_data["start"]
          < evidence_data["end"]
          <= len(sources[evidence_data["source"]]),
          "evidence offsets out of bounds",
        )
        require(
          sources[evidence_data["source"]][
            evidence_data["start"] : evidence_data["end"]
          ]
          == evidence_data["quote"],
          "evidence quote mismatch",
        )
        expected = (
          {"context", "response"}
          if roles[finding_entry["atom"]] == "context"
          else {"rationale"}
        )
        require(
          evidence_data["source"] in expected,
          "evidence source incompatible with atom role",
        )
        evidence.append(Evidence(**evidence_data))
      require(
        finding_entry["value"] == "UNKNOWN" or bool(evidence),
        "known findings require evidence",
      )
      findings.append(
        Finding(
          finding_entry["atom"],
          Truth(finding_entry["value"]),
          tuple(evidence),
          finding_entry["explanation"],
        )
      )
    pairs = data.get("grounding_pairs", [])
    validate_items(pairs)
    for pair in pairs:
      require(
        isinstance(pair, list)
        and len(pair) == 2
        and all(isinstance(name, str) and name in roles for name in pair),
        "invalid grounding pair",
      )
      require(
        roles[pair[0]] == "judge" and roles[pair[1]] == "context",
        "grounding pairs must be judge/context",
      )
    extraction = data["extraction"]
    validate_object(extraction, {"provider", "translation_status"}, {"notes"})
    require_text(extraction["provider"])
    require(
      extraction["translation_status"] in ("VALID", "INVALID", "UNKNOWN"),
      "invalid translation status",
    )
    if "notes" in extraction:
      require_text(extraction["notes"])
    return cls(
      data["id"],
      data["context"],
      data["response"],
      data["rationale"],
      data["verdict"],
      data.get("gold_verdict"),
      protocol["text"],
      tuple(atoms),
      decision,
      constraints,
      requirements,
      tuple(findings),
      tuple((pair_value[0], pair_value[1]) for pair_value in pairs),
      dict(extraction),
    )
