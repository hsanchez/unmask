from typing import cast

import pytest

from unmask.models import (
  Ambiguity,
  AmbiguityInterpretation,
  Finding,
  GuardrailRule,
  Literal,
  RuleSemantics,
  SymbolicModel,
  condition_to_list,
  parse_condition,
)

_CONDITION_RAW = [[{"predicate": "is_educational", "value": True}]]


class TestGuardrailRule:
  def test_valid_rule(self) -> None:
    rule = GuardrailRule(id="rule:1", text="never give medical advice")
    assert rule.id == "rule:1"
    assert rule.text == "never give medical advice"

  @pytest.mark.parametrize("id_value,text_value", [("", "text"), ("rule:1", "")])
  def test_empty_fields_raise(self, id_value: str, text_value: str) -> None:
    with pytest.raises(ValueError, match="must be a non-empty string"):
      _ = GuardrailRule(id=id_value, text=text_value)


class TestLiteral:
  def test_from_dict_defaults_value_true(self) -> None:
    literal = Literal.from_dict({"predicate": "is_educational"})
    assert literal.predicate == "is_educational"
    assert literal.value is True

  def test_from_dict_explicit_value(self) -> None:
    literal = Literal.from_dict({"predicate": "is_educational", "value": False})
    assert literal.value is False

  def test_from_dict_rejects_non_mapping(self) -> None:
    with pytest.raises(TypeError, match="must be mappings"):
      _ = Literal.from_dict("not-a-mapping")

  def test_from_dict_rejects_non_bool_value(self) -> None:
    with pytest.raises(TypeError, match="must be a boolean"):
      _ = Literal.from_dict({"predicate": "is_educational", "value": "true"})

  def test_from_dict_requires_predicate(self) -> None:
    with pytest.raises(ValueError, match="literal.predicate"):
      _ = Literal.from_dict({"value": True})

  def test_to_dict_round_trips(self) -> None:
    literal = Literal(predicate="is_educational", value=False)
    assert literal.to_dict() == {"predicate": "is_educational", "value": False}


class TestParseCondition:
  def test_parses_dnf(self) -> None:
    condition = parse_condition(_CONDITION_RAW, name="safe_condition")
    assert len(condition) == 1
    assert len(condition[0]) == 1
    assert condition[0][0].predicate == "is_educational"

  def test_rejects_non_list(self) -> None:
    with pytest.raises(TypeError, match="AND-clause"):
      _ = parse_condition("not-a-list", name="safe_condition")

  def test_rejects_empty_condition(self) -> None:
    with pytest.raises(ValueError, match="AND-clause"):
      _ = parse_condition(cast("object", []), name="safe_condition")

  def test_rejects_empty_clause(self) -> None:
    with pytest.raises(ValueError, match="literal"):
      _ = parse_condition(cast("object", [[]]), name="safe_condition")

  def test_condition_to_list_round_trips(self) -> None:
    condition = parse_condition(_CONDITION_RAW, name="safe_condition")
    assert condition_to_list(condition) == _CONDITION_RAW


class TestRuleSemantics:
  def test_from_dict_valid(self) -> None:
    rule = RuleSemantics.from_dict(
      {"rule_id": "rule:1", "decision": "block", "condition": _CONDITION_RAW}
    )
    assert rule.rule_id == "rule:1"
    assert rule.decision == "block"

  def test_from_dict_rejects_invalid_decision(self) -> None:
    with pytest.raises(ValueError, match="allow.*block"):
      _ = RuleSemantics.from_dict(
        {"rule_id": "rule:1", "decision": "maybe", "condition": _CONDITION_RAW}
      )


class TestAmbiguity:
  def _interpretation(self, decision: str) -> dict[str, object]:
    return {
      "label": "narrow reading",
      "decision": decision,
      "condition": _CONDITION_RAW,
      "rationale": "because",
    }

  def test_from_dict_valid(self) -> None:
    ambiguity = Ambiguity.from_dict(
      {
        "rule_id": "rule:1",
        "category": "undefined_threshold",
        "excerpt": "substantial",
        "rationale": "no threshold defined",
        "interpretation_a": self._interpretation("block"),
        "interpretation_b": self._interpretation("allow"),
      }
    )
    assert ambiguity.severity == "medium"
    assert isinstance(ambiguity.interpretation_a, AmbiguityInterpretation)

  def test_from_dict_rejects_invalid_severity(self) -> None:
    with pytest.raises(ValueError, match="severity"):
      _ = Ambiguity.from_dict(
        {
          "rule_id": "rule:1",
          "category": "undefined_threshold",
          "excerpt": "substantial",
          "rationale": "no threshold defined",
          "interpretation_a": self._interpretation("block"),
          "interpretation_b": self._interpretation("allow"),
          "severity": "critical",
        }
      )


class TestSymbolicModel:
  def _raw_model(self, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
      "predicates": {"is_educational": "request seeks neutral education"},
      "safe_condition": _CONDITION_RAW,
      "rules": [
        {"rule_id": "rule:1", "decision": "block", "condition": _CONDITION_RAW}
      ],
    }
    base.update(overrides)
    return base

  def test_from_dict_valid(self) -> None:
    model = SymbolicModel.from_dict(self._raw_model())
    assert model.predicates == {"is_educational": "request seeks neutral education"}
    assert model.rules[0].rule_id == "rule:1"

  def test_from_dict_rejects_empty_predicates(self) -> None:
    with pytest.raises(ValueError, match="predicates"):
      _ = SymbolicModel.from_dict(self._raw_model(predicates={}))

  def test_from_dict_rejects_unknown_predicate_reference(self) -> None:
    raw = self._raw_model(
      rules=[
        {
          "rule_id": "rule:1",
          "decision": "block",
          "condition": [[{"predicate": "unknown_predicate", "value": True}]],
        }
      ]
    )
    with pytest.raises(ValueError, match="unknown predicates"):
      _ = SymbolicModel.from_dict(raw)

  def test_to_dict_round_trips_predicates(self) -> None:
    model = SymbolicModel.from_dict(self._raw_model())
    assert model.to_dict()["predicates"] == model.predicates


class TestFinding:
  def test_to_dict_sorts_witness(self) -> None:
    finding = Finding(
      id="over-strict:rule:1",
      bug_type="over_strict",
      severity="high",
      rule_ids=("rule:1",),
      summary="summary",
      rationale="rationale",
      witness={"b": True, "a": False},
    )
    witness = finding.to_dict()["witness"]
    assert isinstance(witness, dict)
    witness_dict = cast("dict[str, object]", witness)
    assert list(witness_dict) == ["a", "b"]
