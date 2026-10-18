"""Benchmark contracts with local fixtures; these are not benchmark parity tests."""

import hashlib
import json
import logging
from dataclasses import replace
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import cast

import pytest

import unmask
import unmask.benches as dataset_presets
import unmask.benchmarks as benchmark_module
from examples.benchmarks import replay_benchmark
from examples.ifbench import adherence_benchmark, load_cases
from tests.helpers import replay_text
from tests.test_library import REGEX
from unmask.benches import IFBench
from unmask.benchmarks import (
  Adherence,
  Benchmark,
  BenchmarkCase,
  BenchmarkKind,
  BenchmarkRun,
  BenchmarkTarget,
  CaseResult,
  FrozenBenchmark,
  Metric,
  Outcome,
  Score,
  decode_replay,
  encode_audit,
  mean_score,
  score_adherence,
)
from unmask.judge import VerificationCase, VerificationResult, adherence
from unmask.service import ScanResult, bugs

KIND = BenchmarkKind.RESPONSE_COMPLIANCE


def test_adherence_configuration_is_separate_from_dataset_presets() -> None:
  assert Adherence.__module__ == "unmask.benchmarks"
  assert "Adherence" not in dataset_presets.__all__
  assert dataset_presets.IFBench is IFBench


def adherence_reference(
  overall: str,
  findings: list[dict[str, object]] | None = None,
) -> dict[str, object]:
  return {
    "overall": overall,
    "findings": findings if findings is not None else [],
    "review_status": "synthetic",
  }


def respond(prompt: str) -> str:
  del prompt
  return '{"greeting":"hello"}'


def score_json_object(
  inputs: dict[str, object], reference: dict[str, object], output: dict[str, object]
) -> Score:
  del inputs
  if reference["instruction_id_list"] != ["example:json-object"]:
    return Score(Outcome.UNSUPPORTED, reason="synthetic fixture constraint unsupported")
  response = output["response"]
  if not isinstance(response, str):
    raise ValueError("response must be text")
  try:
    parsed = cast("object", json.loads(response))
  except ValueError:
    parsed = None
  passed = isinstance(parsed, dict)
  return Score(Outcome.SCORED, float(passed), "PASS" if passed else "FAIL")


def test_ifbench_example_calls_real_unmask_adherence(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  seen: list[str] = []
  real_adherence = unmask.adherence

  def observe(case: VerificationCase) -> VerificationResult:
    seen.append(case.id)
    assert case.gold_verdict is None
    assert "review_status" not in case.to_dict()
    return real_adherence(case)

  monkeypatch.setattr(unmask, "adherence", observe)
  run = adherence_benchmark().evaluate(
    model=unmask.adherence, target_name="unmask.adherence", target_revision="test/v1"
  )
  assert seen == ["incorrect-judge", "insufficient-evidence", "supported"]
  scores = {result.case_id: result.score for result in run.results}
  assert scores["supported"] == Score(Outcome.SCORED, 1.0, "SATISFIED")
  assert scores["incorrect-judge"] == Score(Outcome.SCORED, 1.0, "VIOLATED")
  assert scores["insufficient-evidence"].outcome == Outcome.UNKNOWN
  assert run.metrics == (
    Metric("joint_agreement", 1.0, 2),
    Metric("resolved_coverage", 2 / 3, 3),
  )
  assert run.counts == {
    "total": 3,
    "scored": 2,
    "unknown": 1,
    "unsupported": 0,
    "error": 0,
  }


def test_adherence_scorer_detects_disagreement() -> None:
  score = score_adherence(
    {},
    adherence_reference("VIOLATED"),
    {"summary": {"overall": "SATISFIED"}, "detected_findings": []},
  )
  assert score.value == 0.0
  assert "Overall: expected VIOLATED, got SATISFIED" in score.reason


@pytest.mark.parametrize(
  "actual",
  cast(
    "list[list[dict[str, object]]]",
    [
      [],
      [{"kind": "coverage", "subject": ["r1"]}],
      [{"kind": "procedure", "subject": ["r2"]}],
      [
        {"kind": "procedure", "subject": ["r1"]},
        {"kind": "coverage", "subject": ["r1"]},
      ],
    ],
  ),
)
def test_adherence_scorer_rejects_wrong_finding_sets(
  actual: list[dict[str, object]],
) -> None:
  reference = adherence_reference(
    "VIOLATED", [{"kind": "procedure", "subject": ["r1"]}]
  )
  score = score_adherence(
    {}, reference, {"summary": {"overall": "VIOLATED"}, "detected_findings": actual}
  )
  assert score.value == 0.0
  assert "findings:" in score.reason


def test_grounding_finding_keys_ignore_summary_and_position() -> None:
  case = next(case for case in load_cases() if case.id == "incorrect-judge")
  result = unmask.adherence(decode_replay(case.inputs))
  changed = replace(
    result,
    checks=tuple(
      replace(
        check,
        id="grounding:999",
        summary="Reworded",
        atoms=tuple(reversed(check.atoms)),
      )
      if check.dimension == "grounding"
      else check
      for check in result.checks
    ),
  )
  assert (
    encode_audit(changed)["detected_findings"]
    == encode_audit(result)["detected_findings"]
  )
  reference = adherence_reference(
    "VIOLATED", [{"kind": "grounding", "subject": ["judge_json", "context_json"]}]
  )
  assert score_adherence({}, reference, encode_audit(changed)).value == 1.0


@pytest.mark.parametrize("field", ["findings", "review_status"])
def test_adherence_reference_is_validated_before_target(field: str) -> None:
  original = load_cases()[0]
  reference = original.reference
  del reference[field]
  invalid = BenchmarkCase(original.id, original.task, original.inputs, reference)
  called: list[str] = []

  def observe(case: VerificationCase) -> VerificationResult:
    called.append(case.id)
    return unmask.adherence(case)

  benchmark = Adherence(
    name="invalid-reference", dataset_revision="v1", load=lambda: (invalid,)
  )
  with pytest.raises(ValueError):
    _ = benchmark.evaluate(
      model=observe, target_name="unmask.adherence", target_revision="v1"
    )
  assert called == []


@pytest.mark.parametrize(
  "findings",
  cast(
    "list[object]",
    [
      None,
      [{"kind": "procedure", "subject": []}],
      [{"kind": "procedure", "subject": ["r1", "r1"]}],
      [
        {"kind": "procedure", "subject": ["r1"]},
        {"kind": "procedure", "subject": ["r1"]},
      ],
      [{"kind": "procedure", "subject": [1]}],
      [{"kind": "procedure", "subject": ["r1"], "extra": True}],
    ],
  ),
)
def test_adherence_rejects_malformed_or_duplicate_finding_keys(
  findings: object,
) -> None:
  reference = adherence_reference("VIOLATED")
  reference["findings"] = findings
  with pytest.raises(ValueError):
    _ = score_adherence(
      {}, reference, {"summary": {"overall": "VIOLATED"}, "detected_findings": []}
    )


def test_unknown_findings_do_not_earn_agreement_credit() -> None:
  reference = adherence_reference("UNKNOWN", [{"kind": "procedure", "subject": ["r1"]}])
  score = score_adherence(
    {}, reference, {"summary": {"overall": "UNKNOWN"}, "detected_findings": []}
  )
  assert score.outcome == Outcome.UNKNOWN
  assert score.value is None
  assert "Missing findings" in score.reason


@pytest.mark.parametrize("status", cast("list[object]", [None, "", "reviewed", True]))
def test_adherence_requires_recognized_review_status(status: object) -> None:
  reference = adherence_reference("SATISFIED")
  reference["review_status"] = status
  with pytest.raises(ValueError, match="review_status"):
    _ = score_adherence(
      {}, reference, {"summary": {"overall": "SATISFIED"}, "detected_findings": []}
    )


def test_adherence_errors_reduce_resolved_coverage() -> None:
  def fail(case: VerificationCase) -> VerificationResult:
    raise RuntimeError(f"failed {case.id}")

  run = adherence_benchmark().evaluate(
    model=fail, target_name="failure", target_revision="v1"
  )
  assert run.counts["error"] == 3
  assert run.metrics == (
    Metric("joint_agreement", None, 0),
    Metric("resolved_coverage", 0.0, 3),
  )


def test_callable_model_evaluation() -> None:
  run = definition().evaluate(
    model=answer, target_name="callable", target_revision="v1"
  )
  assert run.target_name == "callable"
  assert run.counts["scored"] == 8


def test_callable_requires_explicit_identity() -> None:
  with pytest.raises(ValueError, match="target_name"):
    _ = definition().evaluate(model=answer)
  with pytest.raises(ValueError, match="target_revision"):
    _ = definition().evaluate(model=answer, target_name="model")


def test_target_and_model_are_exclusive() -> None:
  with pytest.raises(ValueError, match="either target or model"):
    _ = definition().evaluate(target(), model=answer)
  with pytest.raises(ValueError, match="require model"):
    _ = definition().evaluate(target(), target_name="unused")


def test_ifbench_fixture_uses_existing_runner() -> None:
  directory = Path(__file__).parent.parent / "data/fixtures"
  digest = hashlib.sha256((directory / "IFBench_test.jsonl").read_bytes()).hexdigest()
  benchmark = IFBench(
    data_dir=directory,
    dataset_sha256=digest,
    score=score_json_object,
    scoring_id="synthetic-json/v1",
  )
  frozen = benchmark.freeze()
  assert frozen.cases[0].inputs == {"prompt": "Return a JSON object with a greeting."}
  assert frozen.cases[0].reference == {
    "instruction_id_list": ["example:json-object"],
    "kwargs": [{}],
  }
  run = benchmark.evaluate(model=respond, target_name="demo", target_revision="v1")
  assert run.metrics == (Metric("mean_score", 1.0, 1),)
  assert BenchmarkRun.from_json(run.to_json()).frozen == frozen


def test_ifbench_checksum_precedes_target(tmp_path: Path) -> None:
  _ = (tmp_path / "IFBench_test.jsonl").write_text("{}", encoding="utf-8")
  benchmark = IFBench(data_dir=tmp_path, score=score_json_object, scoring_id="demo/v1")
  with pytest.raises(ValueError, match="checksum mismatch"):
    _ = benchmark.evaluate(model=respond, target_name="demo", target_revision="v1")


@pytest.mark.parametrize(
  "row",
  cast(
    "list[dict[str, object]]",
    [
      {},
      {"key": "1", "prompt": "hello", "instruction_id_list": [], "kwargs": []},
      {"key": "1", "prompt": "hello", "instruction_id_list": ["a"], "kwargs": []},
      {"key": "1", "prompt": "hello", "instruction_id_list": ["a"], "kwargs": [1]},
      {"key": "1", "prompt": "hello", "instruction_id_list": [1], "kwargs": [{}]},
      {"key": 1, "prompt": "hello", "instruction_id_list": ["a"], "kwargs": [{}]},
    ],
  ),
)
def test_ifbench_rejects_invalid_rows(tmp_path: Path, row: dict[str, object]) -> None:
  data = json.dumps(row).encode("utf-8")
  _ = (tmp_path / "IFBench_test.jsonl").write_bytes(data)
  benchmark = IFBench(
    data_dir=tmp_path,
    dataset_sha256=hashlib.sha256(data).hexdigest(),
    score=score_json_object,
    scoring_id="demo/v1",
  )
  with pytest.raises(ValueError, match="IFBench_test.jsonl:1:"):
    _ = benchmark.freeze()


def test_ifbench_replay_does_not_read_source(tmp_path: Path) -> None:
  directory = Path(__file__).parent.parent / "data/fixtures"
  data = (directory / "IFBench_test.jsonl").read_bytes()
  source = tmp_path / "IFBench_test.jsonl"
  _ = source.write_bytes(data)
  benchmark = IFBench(
    data_dir=tmp_path,
    dataset_sha256=hashlib.sha256(data).hexdigest(),
    score=score_json_object,
    scoring_id="demo/v1",
  )
  frozen = benchmark.freeze()
  source.unlink()
  run = benchmark.evaluate(
    model=respond,
    target_name="demo",
    target_revision="v1",
    frozen=frozen,
  )
  assert run.counts["scored"] == 1


def test_ifbench_has_no_constructor_io(tmp_path: Path) -> None:
  benchmark = IFBench(data_dir=tmp_path, score=score_json_object, scoring_id="demo/v1")
  with pytest.raises(FileNotFoundError):
    _ = benchmark.freeze()


def decode(inputs: dict[str, object]) -> str:
  if set(inputs) != {"prompt"} or not isinstance(inputs["prompt"], str):
    raise ValueError("input must contain only a text prompt")
  return inputs["prompt"]


def encode(output: str) -> dict[str, object]:
  if not isinstance(cast("object", output), str):
    raise ValueError("output must be text")
  return {"answer": output}


def score_answer(
  inputs: dict[str, object], reference: dict[str, object], output: dict[str, object]
) -> Score:
  del inputs
  answer = output["answer"]
  if answer == "unknown":
    return Score(Outcome.UNKNOWN, reason="target did not decide")
  if answer == "unsupported":
    return Score(Outcome.UNSUPPORTED, reason="task unsupported by scorer")
  if not isinstance(answer, str):
    raise ValueError("answer must be text")
  return Score(Outcome.SCORED, float(answer == reference["answer"]), answer)


def cases() -> tuple[BenchmarkCase, ...]:
  return tuple(
    BenchmarkCase(
      str(index),
      "even" if index % 2 == 0 else "odd",
      {"prompt": f"prompt-{index}"},
      {"answer": f"answer-{index}"},
    )
    for index in range(8)
  )


def definition() -> Benchmark[str, str]:
  return Benchmark(
    "local-contract-fixture",
    "fixture-v1",
    "exact-match/mean-resolved/v1",
    KIND,
    cases,
    decode,
    encode,
    score_answer,
  )


def answer(prompt: str) -> str:
  return prompt.replace("prompt", "answer")


def target() -> BenchmarkTarget[str, str]:
  return BenchmarkTarget("local", "v1", KIND, answer)


def object_json(text: str) -> dict[str, object]:
  return cast("dict[str, object]", json.loads(text))


def test_public_exports() -> None:
  assert unmask.Benchmark is Benchmark
  assert unmask.BenchmarkRun is BenchmarkRun


def test_freeze_is_repeatable_across_loader_order_and_saves_ids() -> None:
  benchmark = definition()
  first = benchmark.freeze(sample_size=4, seed=73)
  reversed_source = replace(benchmark, load=lambda: reversed(cases()))
  assert first == reversed_source.freeze(sample_size=4, seed=73)
  assert len(first.cases) == 4
  assert len({case.id for case in first.cases}) == 4
  assert first != benchmark.freeze(sample_size=4, seed=74)
  restored = FrozenBenchmark.from_json(first.to_json())
  assert restored == first
  assert restored.fingerprint == first.fingerprint


def test_task_selection_is_explicit_and_sampling_is_bounded() -> None:
  sample = definition().freeze(tasks=("odd",), sample_size=3, seed=2)
  assert len(sample.cases) == 3
  assert {case.task for case in sample.cases} == {"odd"}


@pytest.mark.parametrize("size", [0, -1, 9, True, 1.5])
def test_invalid_sample_size(size: object) -> None:
  with pytest.raises(ValueError, match="sample_size"):
    _ = definition().freeze(sample_size=cast("int", size))


@pytest.mark.parametrize("tasks", [(), ("missing",), ("odd", "odd"), (1,), ["odd"]])
def test_invalid_task_selection(tasks: object) -> None:
  with pytest.raises(ValueError):
    _ = definition().freeze(tasks=cast("tuple[str, ...]", tasks))


@pytest.mark.parametrize("seed", [True, 1.2, "0"])
def test_invalid_seed(seed: object) -> None:
  with pytest.raises(ValueError, match="seed"):
    _ = definition().freeze(seed=cast("int", seed))


def test_empty_and_duplicate_data_rejected_before_targets() -> None:
  with pytest.raises(ValueError, match="non-empty"):
    _ = replace(definition(), load=lambda: ()).evaluate(target())
  with pytest.raises(ValueError, match="duplicate"):
    _ = replace(definition(), load=lambda: (cases()[0], cases()[0])).freeze()


def test_invalid_mapped_input_rejected_before_any_target_call() -> None:
  bad = BenchmarkCase("bad", "even", {"prompt": 3}, {})
  called: list[str] = []

  def observe(prompt: str) -> str:
    called.append(prompt)
    return answer(prompt)

  observed = replace(target(), call=observe)
  with pytest.raises(ValueError, match="only a text prompt"):
    _ = replace(definition(), load=lambda: (cases()[0], bad)).evaluate(observed)
  assert called == []


def test_invalid_replay_input_rejected_before_any_target_call() -> None:
  sample = replace(definition().freeze(), cases=(BenchmarkCase("bad", "even", {}, {}),))
  called: list[str] = []

  def observe(prompt: str) -> str:
    called.append(prompt)
    return prompt

  with pytest.raises(ValueError):
    _ = definition().evaluate(
      replace(target(), call=observe),
      frozen=sample,
    )
  assert called == []


def test_case_payloads_are_detached_from_mutation() -> None:
  inputs: dict[str, object] = {"nested": ["original"]}
  reference: dict[str, object] = {"answer": "private"}
  case = BenchmarkCase("id", "task", inputs, reference)
  inputs["nested"] = []
  reference["answer"] = "changed"
  exported = case.inputs
  cast("list[object]", exported["nested"]).append("mutated")
  case.reference["answer"] = "mutated"
  assert case.inputs == {"nested": ["original"]}
  assert case.reference == {"answer": "private"}


def test_reference_never_enters_decoder_or_target() -> None:
  observed: list[dict[str, object]] = []

  def spy(inputs: dict[str, object]) -> str:
    observed.append(inputs)
    assert set(inputs) == {"prompt"}
    assert "answer" not in inputs
    return decode(inputs)

  run = replace(definition(), decode_input=spy).evaluate(target())
  assert observed
  assert run.counts["scored"] == 8
  assert run.metrics == (Metric("mean_score", 1.0, 8),)


def test_scorer_mutation_cannot_change_saved_input_reference_or_output() -> None:
  def mutate(
    inputs: dict[str, object], reference: dict[str, object], output: dict[str, object]
  ) -> Score:
    original = score_answer(inputs, reference, output)
    inputs.clear()
    reference.clear()
    output.clear()
    return original

  sample = definition().freeze()
  run = replace(definition(), score=mutate).evaluate(target(), frozen=sample)
  assert sample == definition().freeze()
  assert run.results[0].output == {"answer": "answer-0"}
  output = run.results[0].output
  assert output is not None
  output.clear()
  assert run.results[0].output == {"answer": "answer-0"}


def test_replay_uses_saved_payloads_without_loader(tmp_path: Path) -> None:
  def unavailable() -> tuple[BenchmarkCase, ...]:
    raise RuntimeError("dataset unavailable")

  sample = definition().freeze(sample_size=3, seed=42)
  path = tmp_path / "sample.json"
  _ = path.write_text(sample.to_json())
  saved = FrozenBenchmark.from_json(path.read_text())
  replay = replace(definition(), load=unavailable).evaluate(target(), frozen=saved)
  assert tuple(result.case_id for result in replay.results) == tuple(
    case.id for case in sample.cases
  )
  assert replay.metrics == (Metric("mean_score", 1.0, 3),)
  assert replay.python_version
  assert replay.unmask_version
  assert replay.elapsed_seconds >= 0
  assert all(result.elapsed_seconds >= 0 for result in replay.results)
  assert BenchmarkRun.from_json(replay.to_json()) == replay


@pytest.mark.parametrize(
  "kind", [BenchmarkKind.JUDGE_AUDIT, BenchmarkKind.BUG_DETECTION]
)
def test_incompatible_target_rejected_before_loading(kind: BenchmarkKind) -> None:
  def fail() -> tuple[BenchmarkCase, ...]:
    pytest.fail("loader called for incompatible target")

  with pytest.raises(ValueError, match="contract"):
    _ = replace(definition(), load=fail).evaluate(replace(target(), kind=kind))


@pytest.mark.parametrize(
  "field,value",
  [
    ("name", "different"),
    ("dataset_revision", "v2"),
    ("scoring_id", "variant-v2"),
    ("kind", BenchmarkKind.JUDGE_AUDIT),
  ],
)
def test_replay_identity_mismatch(field: str, value: object) -> None:
  sample = replace(definition().freeze(), **{field: value})
  with pytest.raises(ValueError, match="identity mismatch"):
    _ = definition().evaluate(target(), frozen=sample)


def test_counts_denominators_and_failure_phases() -> None:
  def mixed(prompt: str) -> str:
    if prompt == "prompt-0":
      raise RuntimeError("target unavailable")
    return {
      "prompt-1": "unknown",
      "prompt-2": "unsupported",
      "prompt-3": "wrong",
      "prompt-4": "scorer-fails",
    }.get(prompt, answer(prompt))

  def scorer(
    inputs: dict[str, object], reference: dict[str, object], output: dict[str, object]
  ) -> Score:
    if output["answer"] == "scorer-fails":
      raise ValueError("bad reference")
    return score_answer(inputs, reference, output)

  run = replace(definition(), score=scorer).evaluate(replace(target(), call=mixed))
  assert run.counts == {
    "total": 8,
    "scored": 4,
    "unknown": 1,
    "unsupported": 1,
    "error": 2,
  }
  assert run.metrics == (Metric("mean_score", 0.75, 4),)
  assert run.results[0].score.reason == "RuntimeError: target unavailable"
  assert run.results[0].error_phase == "target"
  assert run.results[0].output is None
  assert run.results[4].error_phase == "scorer"
  assert run.results[4].output == {"answer": "scorer-fails"}
  assert run.results[4].score.reason == "ValueError: bad reference"
  assert BenchmarkRun.from_json(run.to_json()) == run


def test_all_unknown_has_no_numeric_score() -> None:
  def unknown(prompt: str) -> str:
    del prompt
    return "unknown"

  run = definition().evaluate(replace(target(), call=unknown))
  assert run.counts["unknown"] == 8
  assert run.metrics == (Metric("mean_score", None, 0),)
  assert mean_score(()) == (Metric("mean_score", None, 0),)


def test_custom_aggregation_preserves_its_denominator() -> None:
  def aggregate(results: tuple[CaseResult, ...]) -> tuple[Metric, ...]:
    total = len(results)
    return (
      Metric(
        "coverage",
        sum(result.score.outcome == Outcome.SCORED for result in results) / total,
        total,
      ),
    )

  run = replace(definition(), aggregate=aggregate, scoring_id="coverage/v1").evaluate(
    target()
  )
  assert run.metrics == (Metric("coverage", 1.0, 8),)


def test_aggregation_failure_is_visible() -> None:
  def broken(results: tuple[CaseResult, ...]) -> tuple[Metric, ...]:
    del results
    raise RuntimeError("aggregate failed")

  with pytest.raises(RuntimeError, match="aggregate failed"):
    _ = replace(definition(), aggregate=broken).evaluate(target())


def test_loader_failure_is_visible() -> None:
  def broken() -> tuple[BenchmarkCase, ...]:
    raise OSError("source missing")

  with pytest.raises(OSError, match="source missing"):
    _ = replace(definition(), load=broken).evaluate(target())


def test_keyboard_interrupt_is_not_recorded_as_target_error() -> None:
  def interrupt(prompt: str) -> str:
    del prompt
    raise KeyboardInterrupt

  with pytest.raises(KeyboardInterrupt):
    _ = definition().evaluate(replace(target(), call=interrupt))


@pytest.mark.parametrize(
  "bad", [None, "invalid", Score(Outcome.ERROR, reason="fake error")]
)
def test_malformed_scorer_result_recorded_as_scorer_error(bad: object) -> None:
  def invalid(
    inputs: dict[str, object], reference: dict[str, object], output: dict[str, object]
  ) -> Score:
    del inputs, reference, output
    return cast("Score", bad)

  run = replace(definition(), score=invalid).evaluate(target())
  assert run.counts["error"] == 8
  assert all(result.error_phase == "scorer" for result in run.results)


def test_invalid_output_is_a_target_error() -> None:
  def invalid(prompt: str) -> str:
    del prompt
    return cast("str", cast("object", 4))

  run = definition().evaluate(replace(target(), call=invalid))
  assert run.counts["error"] == 8
  assert all(result.error_phase == "target" for result in run.results)


def test_paired_comparison_reports_decision_and_unknown_transitions() -> None:
  benchmark = definition()
  before = benchmark.evaluate(target())

  def changed(prompt: str) -> str:
    if prompt == "prompt-0":
      return "unknown"
    if prompt == "prompt-1":
      return "changed"
    return answer(prompt)

  after = benchmark.evaluate(
    replace(target(), revision="v2", call=changed), frozen=before.frozen
  )
  comparison = before.compare(after)
  assert comparison["paired_cases"] == 8
  assert comparison["changed_decisions"] == 2
  changes = cast("list[dict[str, object]]", comparison["changes"])
  assert [change["case_id"] for change in changes] == ["0", "1"]
  assert cast("dict[str, object]", changes[0]["after"])["outcome"] == "unknown"
  assert before.compare(BenchmarkRun.from_json(before.to_json()))["changes"] == []


@pytest.mark.parametrize(
  "change", ["input", "reference", "id", "order", "revision", "scoring", "task", "kind"]
)
def test_comparison_rejects_unpaired_or_semantically_changed_runs(change: str) -> None:
  first = definition().evaluate(target())
  original = first.frozen.cases[0]
  modified = first.frozen
  if change in {"input", "reference", "id", "task"}:
    replacement = BenchmarkCase(
      "changed" if change == "id" else original.id,
      "changed" if change == "task" else original.task,
      {"prompt": "changed"} if change == "input" else original.inputs,
      {"answer": "changed"} if change == "reference" else original.reference,
    )
    modified = replace(modified, cases=(replacement, *modified.cases[1:]))
  elif change == "order":
    modified = replace(modified, cases=tuple(reversed(modified.cases)))
  elif change == "revision":
    modified = replace(modified, dataset_revision="v2")
  elif change == "scoring":
    modified = replace(modified, scoring_id="variant/v2")
  else:
    modified = replace(modified, kind=BenchmarkKind.JUDGE_AUDIT)
  results = tuple(
    replace(result, case_id=case.id)
    for result, case in zip(first.results, modified.cases, strict=True)
  )
  second = replace(first, frozen=modified, results=results)
  with pytest.raises(ValueError, match="identical frozen"):
    _ = first.compare(second)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_invalid_scores(value: float) -> None:
  with pytest.raises(ValueError):
    _ = Score(Outcome.SCORED, value, "pass")


@pytest.mark.parametrize(
  "outcome,value,decision,reason",
  [
    (Outcome.SCORED, None, "pass", ""),
    (Outcome.SCORED, 1.0, None, ""),
    (Outcome.UNKNOWN, 0.0, None, "unknown"),
    (Outcome.UNSUPPORTED, None, None, ""),
    ("scored", 1.0, "pass", ""),
  ],
)
def test_score_invariants(
  outcome: object, value: float | None, decision: str | None, reason: str
) -> None:
  with pytest.raises(ValueError):
    _ = Score(cast("Outcome", outcome), value, decision, reason)


@pytest.mark.parametrize(
  "value,denominator", [(None, 1), (0.0, 0), (1.0, -1), (float("nan"), 1), (1.0, True)]
)
def test_metric_invariants(value: float | None, denominator: int) -> None:
  with pytest.raises(ValueError):
    _ = Metric("test", value, denominator)


@pytest.mark.parametrize(
  "payload",
  [{"bad": float("nan")}, {"bad": {1: "key"}}, {"bad": object()}, {"bad": (1, 2)}],
)
def test_non_json_case_payload_rejected(payload: dict[str, object]) -> None:
  with pytest.raises(ValueError):
    _ = BenchmarkCase("id", "task", payload, {})


@pytest.mark.parametrize(
  "field", ["schema_version", "fingerprint", "results", "metrics", "extra"]
)
def test_saved_run_rejects_invalid_structure(field: str) -> None:
  run = definition().evaluate(target())
  data = object_json(run.to_json())
  data[field] = {
    "schema_version": True,
    "fingerprint": "wrong",
    "results": [],
    "metrics": [{"name": "score", "value": None, "denominator": 3}],
    "extra": 1,
  }[field]
  with pytest.raises(ValueError):
    _ = BenchmarkRun.from_json(json.dumps(data))


def test_run_rejects_missing_result_and_duplicate_metrics() -> None:
  run = definition().evaluate(target())
  with pytest.raises(ValueError, match="match frozen"):
    _ = replace(run, results=run.results[:-1])
  with pytest.raises(ValueError, match="duplicate metric"):
    _ = replace(run, metrics=(run.metrics[0], run.metrics[0]))


def test_judge_adapter_uses_existing_api_without_gold_leakage() -> None:
  replay = VerificationCase.from_json(replay_text()).to_dict()
  _ = replay.pop("gold_verdict", None)
  case = BenchmarkCase("judge", "adherence", replay, {"overall": "SATISFIED"})
  observed: list[VerificationCase] = []

  def decode_replay(inputs: dict[str, object]) -> VerificationCase:
    if "gold_verdict" in inputs:
      raise ValueError("gold must not enter target input")
    return VerificationCase.from_json(json.dumps(inputs))

  def encode_audit(result: VerificationResult) -> dict[str, object]:
    return {"overall": result.overall, "status": result.status}

  def check(
    inputs: dict[str, object], reference: dict[str, object], output: dict[str, object]
  ) -> Score:
    del inputs
    overall = str(output["overall"])
    if overall == "UNKNOWN":
      return Score(Outcome.UNKNOWN, reason="audit unresolved")
    return Score(Outcome.SCORED, float(overall == reference["overall"]), overall)

  def audit_target(value: VerificationCase) -> VerificationResult:
    observed.append(value)
    assert value.gold_verdict is None
    return adherence(value)

  benchmark = Benchmark(
    "replay-fixture",
    "v1",
    "overall-match/v1",
    BenchmarkKind.JUDGE_AUDIT,
    lambda: (case,),
    decode_replay,
    encode_audit,
    check,
  )
  run = benchmark.evaluate(
    BenchmarkTarget("adherence", "v1", BenchmarkKind.JUDGE_AUDIT, audit_target)
  )
  assert observed
  assert run.metrics == (Metric("mean_score", 1.0, 1),)
  assert run.results[0].output == encode_audit(adherence(decode_replay(replay)))


def test_bug_adapter_uses_existing_scan_result() -> None:
  def decode_spec(inputs: dict[str, object]) -> str:
    value = inputs["specification"]
    if not isinstance(value, str):
      raise ValueError("specification must be text")
    return value

  def encode_scan(result: ScanResult) -> dict[str, object]:
    return {"findings": sorted({finding.bug_type for finding in result.findings})}

  def check(
    inputs: dict[str, object], reference: dict[str, object], output: dict[str, object]
  ) -> Score:
    del inputs
    return Score(
      Outcome.SCORED,
      float(reference["findings"] == output["findings"]),
      json.dumps(output["findings"]),
    )

  case = BenchmarkCase(
    "regex", "over_strict", {"specification": REGEX}, {"findings": ["over_strict"]}
  )
  benchmark = Benchmark(
    "regex-fixture",
    "v1",
    "finding-set-match/v1",
    BenchmarkKind.BUG_DETECTION,
    lambda: (case,),
    decode_spec,
    encode_scan,
    check,
  )
  run = benchmark.evaluate(
    BenchmarkTarget("bugs", "v1", BenchmarkKind.BUG_DETECTION, bugs)
  )
  assert run.results[0].output == encode_scan(bugs(REGEX))
  assert run.metrics == (Metric("mean_score", 1.0, 1),)


@pytest.mark.parametrize(
  "field,value",
  [
    ("schema_version", 2),
    ("schema_version", True),
    ("name", "changed"),
    ("dataset_revision", "changed"),
    ("scoring_id", "changed"),
    ("kind", "invalid"),
    ("cases", []),
    ("fingerprint", "invalid"),
  ],
)
def test_frozen_manifest_rejects_malformed_or_tampered_data(
  field: str, value: object
) -> None:
  data = object_json(definition().freeze().to_json())
  data[field] = value
  with pytest.raises(ValueError):
    _ = FrozenBenchmark.from_json(json.dumps(data))


@pytest.mark.parametrize("field", ["inputs", "reference", "id", "task"])
def test_frozen_payload_tampering_rejected(field: str) -> None:
  data = object_json(definition().freeze().to_json())
  first = cast("list[dict[str, object]]", data["cases"])[0]
  first[field] = {} if field in {"inputs", "reference"} else "tampered"
  with pytest.raises(ValueError, match="fingerprint"):
    _ = FrozenBenchmark.from_json(json.dumps(data))


def test_duplicate_json_keys_are_rejected() -> None:
  data = definition().freeze().to_json()
  duplicate = data.replace(
    '"schema_version": 1', '"schema_version": 99, "schema_version": 1'
  )
  with pytest.raises(ValueError, match="duplicate JSON field"):
    _ = FrozenBenchmark.from_json(duplicate)


def test_cyclic_case_data_rejected_clearly() -> None:
  inputs: dict[str, object] = {}
  inputs["self"] = inputs
  with pytest.raises(ValueError):
    _ = BenchmarkCase("id", "task", inputs, {})


@pytest.mark.parametrize("text", ["null", "[]", "{", '{"bad": NaN}'])
def test_invalid_json_documents(text: str) -> None:
  with pytest.raises(ValueError):
    _ = FrozenBenchmark.from_json(text)
  with pytest.raises(ValueError):
    _ = BenchmarkRun.from_json(text)


@pytest.mark.parametrize(
  "field,value",
  [
    ("score", {"outcome": "invalid", "value": None, "decision": None, "reason": "bad"}),
    ("elapsed_seconds", -1),
    ("output_json", "[]"),
    ("output_json", None),
    ("error_phase", "target"),
  ],
)
def test_saved_case_results_reject_malformed_records(field: str, value: object) -> None:
  data = object_json(definition().evaluate(target()).to_json())
  cast("list[dict[str, object]]", data["results"])[0][field] = value
  with pytest.raises(ValueError):
    _ = BenchmarkRun.from_json(json.dumps(data))


@pytest.mark.parametrize(
  "timestamp", ["yesterday", "2026-10-18", "2026-10-18T12:00:00"]
)
def test_saved_run_requires_aware_timestamp(timestamp: str) -> None:
  data = object_json(definition().evaluate(target()).to_json())
  data["started_at"] = timestamp
  with pytest.raises(ValueError):
    _ = BenchmarkRun.from_json(json.dumps(data))


def test_missing_aggregation_metrics_are_rejected() -> None:
  def empty(results: tuple[CaseResult, ...]) -> tuple[Metric, ...]:
    del results
    return ()

  with pytest.raises(ValueError, match="non-empty tuple of Metric"):
    _ = replace(definition(), aggregate=empty).evaluate(target())


def test_unwrapped_target_has_clear_error() -> None:
  with pytest.raises(ValueError, match="BenchmarkTarget"):
    _ = definition().evaluate(cast("BenchmarkTarget[str, str]", cast("object", answer)))


def test_invalid_frozen_argument_has_clear_error() -> None:
  with pytest.raises(ValueError, match="FrozenBenchmark"):
    _ = definition().evaluate(
      target(), frozen=cast("FrozenBenchmark", cast("object", {}))
    )


def test_unhashable_task_selection_has_clear_error() -> None:
  with pytest.raises(ValueError, match="tasks"):
    _ = definition().freeze(tasks=cast("tuple[str, ...]", cast("object", (["odd"],))))


def test_score_too_large_to_convert_has_clear_error() -> None:
  with pytest.raises(ValueError, match="finite number"):
    _ = Score(Outcome.SCORED, 10**1000, "pass")


@pytest.mark.parametrize(
  "fixture", ["unknown_support.json", "invalid_translation.json"]
)
def test_example_adapter_preserves_unresolved_replays(fixture: str) -> None:
  inputs = VerificationCase.from_json(replay_text(fixture)).to_dict()
  _ = inputs.pop("gold_verdict", None)
  result = adherence(decode_replay(inputs))
  score = score_adherence(
    inputs, adherence_reference("SATISFIED"), encode_audit(result)
  )
  assert score.outcome == Outcome.UNKNOWN
  assert score.value is None


def test_example_adapter_rejects_gold_in_target_inputs() -> None:
  inputs = VerificationCase.from_json(replay_text()).to_dict()
  with pytest.raises(ValueError, match="gold verdict"):
    _ = decode_replay(inputs)


def test_example_definition_freezes_without_gold_reference_leak() -> None:
  sample = replay_benchmark().freeze()
  assert len(sample.cases) == 1
  assert "gold_verdict" not in sample.cases[0].inputs
  assert sample.cases[0].reference == adherence_reference("SATISFIED")


@pytest.mark.parametrize("replay", [False, True])
def test_inputs_are_validated_once_before_execution_and_decoded_fresh(
  replay: bool,
) -> None:
  events: list[tuple[str, str]] = []

  def observe_decode(inputs: dict[str, object]) -> str:
    prompt = decode(inputs)
    events.append(("decode", prompt))
    return prompt

  def observe_target(prompt: str) -> str:
    events.append(("target", prompt))
    return answer(prompt)

  benchmark = replace(definition(), decode_input=observe_decode)
  sample = definition().freeze() if replay else None
  _ = benchmark.evaluate(replace(target(), call=observe_target), frozen=sample)
  prompts = [f"prompt-{index}" for index in range(8)]
  assert events[:8] == [("decode", prompt) for prompt in prompts]
  assert events[8:] == [
    event for prompt in prompts for event in (("decode", prompt), ("target", prompt))
  ]


def test_run_rejects_tampered_nested_sample_payload() -> None:
  data = object_json(definition().evaluate(target()).to_json())
  frozen = cast("dict[str, object]", data["frozen"])
  case = cast("list[dict[str, object]]", frozen["cases"])[0]
  case["reference"] = {"answer": "tampered"}
  with pytest.raises(ValueError, match="fingerprint"):
    _ = BenchmarkRun.from_json(json.dumps(data))


@pytest.mark.parametrize("field", ["inputs", "reference"])
def test_case_rejects_unpaired_surrogates_before_fingerprinting(field: str) -> None:
  payload: dict[str, object] = {"text": chr(0xD800)}
  with pytest.raises(ValueError, match="UTF-8"):
    _ = BenchmarkCase(
      "id",
      "task",
      payload if field == "inputs" else {},
      payload if field == "reference" else {},
    )


def test_escaped_unpaired_surrogate_is_rejected_when_loading() -> None:
  data = object_json(definition().freeze().to_json())
  case = cast("list[dict[str, object]]", data["cases"])[0]
  case["inputs"] = {"prompt": chr(0xDFFF)}
  with pytest.raises(ValueError, match="UTF-8"):
    _ = FrozenBenchmark.from_json(json.dumps(data))


def test_valid_unicode_case_payloads_round_trip() -> None:
  case = BenchmarkCase("café", "中文", {"prompt": "🚫"}, {"answer": "✓"})
  sample = replace(definition(), load=lambda: (case,)).freeze()
  assert FrozenBenchmark.from_json(sample.to_json()) == sample
  assert sample.to_json().encode("utf-8")


@pytest.mark.parametrize("metric_value", [2**53 + 1, 2**100])
def test_large_integer_metrics_round_trip_without_precision_loss(
  metric_value: int,
) -> None:
  run = replace(
    definition().evaluate(target()), metrics=(Metric("total_bytes", metric_value, 1),)
  )
  restored = BenchmarkRun.from_json(run.to_json())
  assert restored.metrics[0].value == metric_value
  assert type(restored.metrics[0].value) is int
  assert restored.to_json() == run.to_json()


def test_callback_failure_has_an_opt_in_debug_traceback(
  caplog: pytest.LogCaptureFixture,
) -> None:
  def fail(prompt: str) -> str:
    raise ValueError(f"failed {prompt}")

  with caplog.at_level(logging.DEBUG, logger="unmask.benchmarks"):
    run = definition().evaluate(replace(target(), call=fail))
  assert run.counts["error"] == 8
  failures = [record for record in caplog.records if record.name == "unmask.benchmarks"]
  assert len(failures) == 8
  assert failures[0].exc_info is not None
  assert "case 0 failed during target" in failures[0].getMessage()


def test_callback_failure_with_invalid_unicode_can_be_saved() -> None:
  def fail(prompt: str) -> str:
    del prompt
    raise RuntimeError(chr(0xD800))

  run = definition().evaluate(replace(target(), call=fail))
  assert run.results[0].score.reason == "RuntimeError: \\ud800"
  assert BenchmarkRun.from_json(run.to_json()) == run


def test_invalid_unicode_reason_is_rejected() -> None:
  with pytest.raises(ValueError, match="UTF-8"):
    _ = Score(Outcome.UNKNOWN, reason=chr(0xD800))


def test_scored_whitespace_reason_remains_supported() -> None:
  assert Score(Outcome.SCORED, 1, "pass", " ").reason == " "


def test_missing_version_metadata_fails_before_loading_or_target_execution(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  def missing(name: str) -> str:
    raise PackageNotFoundError(name)

  def fail_loader() -> tuple[BenchmarkCase, ...]:
    pytest.fail("loader must not run when required metadata is unavailable")

  monkeypatch.setattr(benchmark_module, "version", missing)
  with pytest.raises(PackageNotFoundError):
    _ = replace(definition(), load=fail_loader).evaluate(target())


def test_validated_snapshot_access_does_not_repeat_json_validation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  case = cases()[0]
  result = definition().evaluate(target()).results[0]

  def unexpected(value: object) -> None:
    del value
    pytest.fail("immutable JSON snapshots must not be revalidated on every access")

  monkeypatch.setattr(benchmark_module, "_validate_json", unexpected)
  assert case.inputs == {"prompt": "prompt-0"}
  assert case.reference == {"answer": "answer-0"}
  assert result.output == {"answer": "answer-0"}
