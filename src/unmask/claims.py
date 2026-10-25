"""Typed, finite claim reviews compiled into the existing annotated replay model."""

from __future__ import annotations

import json
import re
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import cast

from .judge import (
  MAX_ATOMS,
  MAX_BYTES,
  Annotation,
  Atom,
  Evidence,
  Expr,
  ProtocolRule,
  Truth,
  ValidationError,
  VerificationCase,
  VerificationFinding,
  require,
  require_text,
  validate_items,
  validate_object,
)

__all__ = [
  "Claim",
  "ClaimBinding",
  "ClaimCase",
  "CompiledClaims",
  "InventoryReview",
  "Message",
  "Relation",
  "RelationReview",
  "ReviewGap",
]

_PROTOCOL = (
  "Identify all factual response claims and explicitly assess each against the "
  "supplied context. Judge support must agree with independent context review. "
  "PASS iff every factual response claim is judged supported. Claim inventory completeness must "
  "be reviewed across the entire response."
)


class Relation(StrEnum):
  """The supported, reviewed relationships in a claim evaluation."""

  ADDRESSES = "addresses"
  JUDGES_SUPPORTED = "judges_supported"
  SUPPORTS = "supports"
  CONTRADICTS = "contradicts"


@dataclass(frozen=True)
class Message:
  """An identified range in context, response, or judge rationale."""

  id: str
  span: Evidence


@dataclass(frozen=True)
class Claim:
  """A reviewed factual claim within a declared response message."""

  id: str
  message: str
  span: Evidence


@dataclass(frozen=True)
class RelationReview:
  """An explicit assessment scoped to messages and backed by exact quotations."""

  id: str
  claim: str
  messages: tuple[str, ...]
  relation: Relation
  value: Truth
  evidence: tuple[Evidence, ...]
  explanation: str


@dataclass(frozen=True)
class InventoryReview:
  """A supplied completeness assessment over named response messages."""

  messages: tuple[str, ...]
  value: Truth
  evidence: tuple[Evidence, ...]
  explanation: str


def _identifier(value: object) -> str:
  identifier = require_text(value)
  require(
    re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", identifier) is not None,
    "invalid entity id",
  )
  return identifier


@contextmanager
def _at_record(label: str) -> Generator[None]:
  try:
    yield
  except ValidationError as error:
    raise ValidationError(f"{label}: {error}") from error


def _records[T](
  value: object, expected: type[T], limit: int = MAX_ATOMS
) -> tuple[T, ...]:
  require(isinstance(value, tuple), "records must be tuples")
  records = cast("tuple[object, ...]", value)
  require(len(records) <= limit, "too many records")
  require(
    all(isinstance(item, expected) for item in records),
    f"expected {expected.__name__} records",
  )
  return cast("tuple[T, ...]", records)


def _scope(
  value: object, messages: dict[str, Message], source: str
) -> tuple[Message, ...]:
  identifiers = _records(value, str)
  require(
    bool(identifiers) and len(set(identifiers)) == len(identifiers),
    "message scope must be non-empty and distinct",
  )
  require(
    all(identifier in messages for identifier in identifiers),
    "dangling message reference",
  )
  selected = tuple(messages[identifier] for identifier in identifiers)
  require(
    all(message.span.source == source for message in selected),
    "message role incompatible with review",
  )
  return selected


def _bounded_text(value: object, budget: list[int]) -> None:
  require(isinstance(value, str), "expected text")
  text = cast("str", value)
  # Character count is a cheap lower bound before UTF-8 encoding or scanning.
  require(len(text) <= budget[0], "typed artifact exceeds byte limit")
  _ = require_text(text)
  budget[0] -= len(text.encode("utf-8"))
  require(budget[0] >= 0, "typed artifact exceeds byte limit")


def _span(span: Evidence, sources: dict[str, str], text_budget: list[int]) -> None:
  require(isinstance(cast("object", span), Evidence), "expected Evidence")
  _ = require_text(span.source)
  _bounded_text(span.quote, text_budget)
  require(span.source in sources, "invalid source document")
  require(type(span.start) is int and type(span.end) is int, "offsets must be integers")
  require(0 <= span.start < span.end <= len(sources[span.source]), "span out of bounds")
  require(
    sources[span.source][span.start : span.end] == span.quote,
    f"evidence quote mismatch at {span.source}[{span.start}:{span.end}]",
  )


def _contained(span: Evidence, message: Message) -> bool:
  return (
    span.source == message.span.source
    and message.span.start <= span.start < span.end <= message.span.end
  )


def _evidence(
  value: object,
  truth: Truth,
  scope: tuple[Message, ...],
  sources: dict[str, str],
  text_budget: list[int],
) -> None:
  require(isinstance(cast("object", truth), Truth), "expected Truth")
  evidence = _records(value, Evidence, 16)
  require(truth == Truth.UNKNOWN or bool(evidence), "known reviews require evidence")
  for index, span in enumerate(evidence):
    with _at_record(f"evidence[{index}]"):
      _span(span, sources, text_budget)
      require(
        any(_contained(span, message) for message in scope),
        "evidence outside message scope",
      )


def _covers(messages: tuple[Message, ...], text: str) -> bool:
  end = 0
  for message in sorted(messages, key=lambda item: item.span.start):
    if message.span.start > end:
      return False
    end = max(end, message.span.end)
  return end == len(text)


def _complete_scope(
  selected: tuple[Message, ...], messages: tuple[Message, ...], source: str, text: str
) -> bool:
  return {message.id for message in selected} == {
    message.id for message in messages if message.span.source == source
  } and _covers(selected, text)


def _json(payload: dict[str, object]) -> str:
  # Check repeated quotation bytes before allocating an expanded JSON string.
  pending: list[object] = [payload]
  size = 0
  while pending:
    item = pending.pop()
    if isinstance(item, str):
      size += len(item.encode("utf-8"))
    elif isinstance(item, dict):
      mapping = cast("dict[str, object]", item)
      pending.extend(mapping.keys())
      pending.extend(mapping.values())
    elif isinstance(item, (tuple, list)):
      pending.extend(cast("tuple[object, ...] | list[object]", item))
    require(size <= MAX_BYTES, "typed artifact exceeds byte limit")
  text = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
  require(len(text.encode("utf-8")) <= MAX_BYTES, "typed artifact exceeds byte limit")
  return text


@dataclass(frozen=True, kw_only=True)
class ClaimCase:
  """A bounded, manually reviewed input for the fixed claim-review protocol.

  False support reviews cover the entire declared context. Missing relationships
  remain unbound. Completeness is supplied, never inferred from a claim list.

  Raises:
    ValidationError: if references, evidence, scopes, or expansion bounds fail.
  """

  id: str
  context: str
  response: str
  rationale: str
  verdict: str
  messages: tuple[Message, ...]
  claims: tuple[Claim, ...]
  reviews: tuple[RelationReview, ...]
  inventory: InventoryReview | None = None

  def __post_init__(self) -> None:
    _ = _identifier(self.id)
    sources = {
      "context": self.context,
      "response": self.response,
      "rationale": self.rationale,
    }
    for source, text in sources.items():
      with _at_record(source):
        _ = require_text(text)
    source_bytes = sum(len(text.encode("utf-8")) for text in sources.values())
    require(source_bytes <= MAX_BYTES, "sources exceed byte limit")
    text_budget = [MAX_BYTES - source_bytes]
    require(self.verdict in ("PASS", "FAIL"), "verdict must be PASS or FAIL")
    messages = _records(self.messages, Message)
    claims = _records(self.claims, Claim)
    reviews = _records(self.reviews, RelationReview, MAX_ATOMS * 2)
    require(3 * len(claims) + 1 <= MAX_ATOMS, "expanded atom limit exceeded")
    require(len(reviews) + 1 <= MAX_ATOMS * 2, "expanded annotation limit exceeded")
    records: tuple[Message | Claim | RelationReview, ...] = (
      *messages,
      *claims,
      *reviews,
    )
    identifiers: set[str] = set()
    for index, record in enumerate(records):
      with _at_record(f"{type(record).__name__} record[{index}]"):
        identifier = _identifier(record.id)
        require(identifier not in identifiers, f"duplicate entity id {identifier!r}")
        identifiers.add(identifier)
    message_map = {message.id: message for message in messages}
    for message in messages:
      with _at_record(f"message {message.id!r}"):
        _span(message.span, sources, text_budget)
    claim_ids = {claim.id for claim in claims}
    for claim in claims:
      with _at_record(f"claim {claim.id!r}"):
        _ = _identifier(claim.message)
        require(claim.message in message_map, "dangling claim message")
        _span(claim.span, sources, text_budget)
        require(
          claim.span.source == "response"
          and _contained(claim.span, message_map[claim.message]),
          "claim outside response message",
        )
    for review in reviews:
      with _at_record(f"review {review.id!r}"):
        _ = _identifier(review.claim)
        require(review.claim in claim_ids, "dangling reviewed claim")
        require(
          isinstance(cast("object", review.relation), Relation), "expected Relation"
        )
        source = (
          "context"
          if review.relation in (Relation.SUPPORTS, Relation.CONTRADICTS)
          else "rationale"
        )
        selected = _scope(review.messages, message_map, source)
        _bounded_text(review.explanation, text_budget)
        _evidence(review.evidence, review.value, selected, sources, text_budget)
        if review.value == Truth.FALSE and review.relation in (
          Relation.SUPPORTS,
          Relation.ADDRESSES,
        ):
          assessment = (
            "support" if review.relation == Relation.SUPPORTS else "addressing"
          )
          require(
            _complete_scope(selected, messages, source, sources[source]),
            f"negative {assessment} requires complete {source} scope",
          )
    if self.inventory is not None:
      require(
        isinstance(cast("object", self.inventory), InventoryReview),
        "expected InventoryReview",
      )
      with _at_record("inventory"):
        selected = _scope(self.inventory.messages, message_map, "response")
        _bounded_text(self.inventory.explanation, text_budget)
        _evidence(
          self.inventory.evidence, self.inventory.value, selected, sources, text_budget
        )
        if self.inventory.value == Truth.TRUE:
          require(
            _complete_scope(selected, messages, "response", self.response),
            "complete inventory requires entire response scope",
          )
    _ = _json(cast("dict[str, object]", asdict(self)))

  def compile(self) -> CompiledClaims:
    """Lower reviewed claims to the existing replay representation.

    Raises:
      ValidationError: if the expanded replay exceeds existing validation bounds.
    """
    atoms = [
      Atom("inventory_complete", "context", "Reviewed claim inventory is complete")
    ]
    inventory = self.inventory
    annotations = [
      Annotation(
        "inventory_complete",
        inventory.value if inventory else Truth.UNKNOWN,
        inventory.evidence if inventory else (),
        inventory.explanation
        if inventory
        else "Inventory completeness has not been reviewed.",
      )
    ]
    complete = Expr("atom", atom="inventory_complete")
    requirements = [
      ProtocolRule(
        "inventory_complete",
        "Review the complete response claim inventory",
        complete,
        complete,
      )
    ]
    bindings: list[ClaimBinding] = []
    pairs: list[tuple[str, str]] = []
    decisions: list[Expr] = []
    for claim in sorted(self.claims, key=lambda item: item.id):
      binding = ClaimBinding(
        claim.id,
        f"judge_{claim.id}",
        f"support_{claim.id}",
        f"addressed_{claim.id}",
        f"claim:{claim.id}:addressed",
      )
      bindings.append(binding)
      atoms.extend(
        (
          Atom(binding.judge_atom, "judge", f"Judge support for claim {claim.id}"),
          Atom(
            binding.context_atom, "context", f"Independent support for claim {claim.id}"
          ),
          Atom(
            binding.addressed_atom, "procedure", f"Judge addresses claim {claim.id}"
          ),
        )
      )
      addressed = Expr("atom", atom=binding.addressed_atom)
      requirements.append(
        ProtocolRule(
          binding.requirement, f"Address claim {claim.id}", addressed, addressed
        )
      )
      pairs.append((binding.judge_atom, binding.context_atom))
      decisions.append(Expr("atom", atom=binding.judge_atom))
    # A partial inventory cannot force PASS from only its listed positive labels.
    if inventory is None or inventory.value != Truth.TRUE:
      atoms.append(
        Atom("unlisted_judgment", "judge", "All unlisted claims are judged supported")
      )
      decisions.append(Expr("atom", atom="unlisted_judgment"))
    binding_map = {binding.claim: binding for binding in bindings}
    for review in sorted(self.reviews, key=lambda item: item.id):
      binding = binding_map[review.claim]
      if review.relation == Relation.CONTRADICTS:
        if review.value != Truth.TRUE:
          continue
        atom, value = binding.context_atom, Truth.FALSE
      else:
        atom = {
          Relation.ADDRESSES: binding.addressed_atom,
          Relation.JUDGES_SUPPORTED: binding.judge_atom,
          Relation.SUPPORTS: binding.context_atom,
        }[review.relation]
        value = review.value
      annotations.append(
        Annotation(atom, value, review.evidence, f"{review.id}: {review.explanation}")
      )
    case = VerificationCase(
      id=self.id,
      context=self.context,
      response=self.response,
      rationale=self.rationale,
      verdict=self.verdict,
      protocol_text=_PROTOCOL,
      atoms=tuple(atoms),
      decision=Expr("and", args=tuple(decisions))
      if decisions
      else Expr("const", value=True),
      requirements=tuple(requirements),
      annotations=tuple(annotations),
      grounding_pairs=tuple(pairs),
      extraction={
        "provider": "caller-supplied-claim-compiler",
        "translation_status": "VALID",
        "notes": "VALID describes the fixed protocol lowering only. Semantic assessments and completeness are unauthenticated caller assertions, not proved or independently reviewed.",
      },
    )
    return CompiledClaims(self, case, tuple(bindings))


@dataclass(frozen=True)
class ClaimBinding:
  """Stable generated identifiers linking solver checks back to one claim."""

  claim: str
  judge_atom: str
  context_atom: str
  addressed_atom: str
  requirement: str


@dataclass(frozen=True)
class ReviewGap:
  """An unresolved review obligation, separate from a proven violation."""

  subject: str
  claim: Claim | None
  summary: str


@dataclass(frozen=True)
class CompiledClaims:
  """An ordinary replay paired with its typed source and deterministic provenance."""

  source: ClaimCase
  case: VerificationCase
  bindings: tuple[ClaimBinding, ...]

  @property
  def review_gaps(self) -> tuple[ReviewGap, ...]:
    """Describe missing assessments and incomplete inventory review."""
    gaps: list[ReviewGap] = []
    inventory = self.source.inventory
    if inventory is None or inventory.value != Truth.TRUE:
      summary = "Claim inventory has not been reviewed"
      if inventory is not None:
        summary = (
          "Claim inventory is incomplete"
          if inventory.value == Truth.FALSE
          else "Claim inventory completeness is unresolved"
        )
      gaps.append(ReviewGap("inventory_complete", None, summary))
    claims = {claim.id: claim for claim in self.source.claims}
    known = {
      annotation.atom
      for annotation in self.case.annotations
      if annotation.value != Truth.UNKNOWN
    }
    observed = {annotation.atom for annotation in self.case.annotations}
    for binding in self.bindings:
      for atom, label in (
        (binding.addressed_atom, "Addressing"),
        (binding.judge_atom, "Judge support"),
        (binding.context_atom, "Independent support"),
      ):
        if atom in known:
          continue
        state = "is unresolved" if atom in observed else "has not been supplied"
        gaps.append(
          ReviewGap(atom, claims[binding.claim], f"{label} assessment {state}")
        )
    return tuple(gaps)

  def claims_for(self, finding: VerificationFinding) -> tuple[Claim, ...]:
    """Locate claims implicated by a finding; inventory findings have no single claim."""
    atoms = {annotation.atom for annotation in finding.conflicting_annotations}
    if finding.check is not None:
      atoms.update(finding.check.atoms)
    identifiers = {
      binding.claim
      for binding in self.bindings
      if atoms.intersection(
        (binding.judge_atom, binding.context_atom, binding.addressed_atom)
      )
    }
    return tuple(
      sorted(
        (claim for claim in self.source.claims if claim.id in identifiers),
        key=lambda claim: claim.id,
      )
    )

  def to_json(self) -> str:
    """Serialize typed inputs and provenance separately from replay version 1.

    Raises:
      ValidationError: if the artifact exceeds the byte limit.
    """
    return _json(
      {
        "schema_version": 1,
        "compiler": "claims-v1",
        "input": asdict(self.source),
        "bindings": [asdict(binding) for binding in self.bindings],
      }
    )

  @classmethod
  def from_json(cls, text: str) -> CompiledClaims:
    """Recompile and check binding consistency; this does not authenticate reviews.

    Raises:
      ValidationError: if the artifact, version, references, or provenance is invalid.
    """
    _ = require_text(text)
    require(len(text.encode("utf-8")) <= MAX_BYTES, "typed artifact exceeds byte limit")
    try:
      payload = cast(
        "object",
        json.loads(text, object_pairs_hook=_unique, parse_constant=_invalid_constant),
      )
    except (ValueError, RecursionError) as error:
      raise ValidationError(f"invalid typed artifact JSON: {error}") from error
    data = validate_object(payload, {"schema_version", "compiler", "input", "bindings"})
    require(
      type(data["schema_version"]) is int
      and data["schema_version"] == 1
      and data["compiler"] == "claims-v1",
      "unsupported typed artifact version",
    )
    raw = validate_object(
      data["input"],
      {
        "id",
        "context",
        "response",
        "rationale",
        "verdict",
        "messages",
        "claims",
        "reviews",
        "inventory",
      },
    )
    messages: list[Message] = []
    for index, item in enumerate(validate_items(raw["messages"])):
      with _at_record(f"messages[{index}]"):
        message = validate_object(item, {"id", "span"})
        messages.append(
          Message(require_text(message["id"]), _decode_span(message["span"]))
        )
    claims: list[Claim] = []
    for index, item in enumerate(validate_items(raw["claims"])):
      with _at_record(f"claims[{index}]"):
        claim = validate_object(item, {"id", "message", "span"})
        claims.append(
          Claim(
            require_text(claim["id"]),
            require_text(claim["message"]),
            _decode_span(claim["span"]),
          )
        )
    reviews: list[RelationReview] = []
    for index, item in enumerate(validate_items(raw["reviews"], MAX_ATOMS * 2)):
      with _at_record(f"reviews[{index}]"):
        review = validate_object(
          item,
          {"id", "claim", "messages", "relation", "value", "evidence", "explanation"},
        )
        reviews.append(
          RelationReview(
            require_text(review["id"]),
            require_text(review["claim"]),
            _decode_ids(review["messages"]),
            _choice(review["relation"], Relation),
            _choice(review["value"], Truth),
            _decode_evidence(review["evidence"]),
            require_text(review["explanation"]),
          )
        )
    inventory: InventoryReview | None = None
    if raw["inventory"] is not None:
      review = validate_object(
        raw["inventory"], {"messages", "value", "evidence", "explanation"}
      )
      inventory = InventoryReview(
        _decode_ids(review["messages"]),
        _choice(review["value"], Truth),
        _decode_evidence(review["evidence"]),
        require_text(review["explanation"]),
      )
    compiled = ClaimCase(
      id=require_text(raw["id"]),
      context=require_text(raw["context"]),
      response=require_text(raw["response"]),
      rationale=require_text(raw["rationale"]),
      verdict=require_text(raw["verdict"]),
      messages=tuple(messages),
      claims=tuple(claims),
      reviews=tuple(reviews),
      inventory=inventory,
    ).compile()
    require(
      data["bindings"] == [asdict(binding) for binding in compiled.bindings],
      "typed artifact binding mismatch",
    )
    _ = compiled.to_json()
    return compiled


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
  result = dict(pairs)
  require(len(result) == len(pairs), "duplicate JSON field")
  return result


def _invalid_constant(value: str) -> object:
  raise ValidationError(f"invalid JSON constant: {value}")


def _choice[T: StrEnum](value: object, expected: type[T]) -> T:
  text = require_text(value)
  try:
    return expected(text)
  except ValueError as error:
    raise ValidationError(f"invalid {expected.__name__}") from error


def _decode_span(value: object) -> Evidence:
  span = validate_object(value, {"source", "start", "end", "quote"})
  require(
    type(span["start"]) is int and type(span["end"]) is int, "offsets must be integers"
  )
  return Evidence(
    require_text(span["source"]),
    cast("int", span["start"]),
    cast("int", span["end"]),
    require_text(span["quote"]),
  )


def _decode_ids(value: object) -> tuple[str, ...]:
  return tuple(require_text(item) for item in validate_items(value))


def _decode_evidence(value: object) -> tuple[Evidence, ...]:
  return tuple(_decode_span(item) for item in validate_items(value, 16))
