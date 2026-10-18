# Benchmark API

`Benchmark` defines a reusable experiment. `BenchmarkRun` records the result,
including frozen cases, outputs, scores, unresolved cases, errors, metrics with
denominators, timing, target identity, and Python/Unmask versions. This API runs
callbacks supplied by your application. Importing it loads no datasets or models.

Run the local example:

```bash
uv run python examples/benchmarks.py
```

The example uses the packaged annotated judge replay and the existing
`unmask.adherence` callable. It is an API demonstration with one fixture, not a
published benchmark or evidence of general judge quality. The IFBench adapter
below loads dataset cases with a separately supplied scorer; no official-score
compatibility, live evaluation, or upstream checker implementation is included.

## IFBench data with your scoring protocol

Named dataset presets live in `unmask.benches`; reusable runner, scoring, task
configuration, and result types live in `unmask.benchmarks`. Each preset returns the same `Benchmark`, with no separate
runner, registry, or subclass hierarchy. Dataset files stay outside the package.

Download the single-turn dataset into `data/ifbench` using the pinned command in
[the data guide](../data/README.md). The adapter uses only the standard library;
it checks the source checksum before any model call and never downloads on import
or during evaluation.

```python
from pathlib import Path
from unmask.benches import IFBench

benchmark = IFBench(
  data_dir=Path("data/ifbench"),
  score=my_scorer,
  scoring_id="my-instruction-protocol/mean-resolved/v1",
)
sample = benchmark.freeze(sample_size=100, seed=42)
result = benchmark.evaluate(
  model=my_model,
  target_name="my-model",
  target_revision="model-and-generation-settings/v1",
  frozen=sample,
)
print(result.metrics, result.counts)
```

`my_model` is a callable accepting one prompt string and returning a response
string. Wrap an SDK's generation method yourself; there is no model-provider
framework or implicit model selection. Alternatively, pass an existing
`BenchmarkTarget` instead of `model` and its identity arguments.

`my_scorer(inputs, reference, output)` returns a `Score`. Inputs contain only
`prompt`; reference contains aligned `instruction_id_list` and `kwargs`; output
contains `response`. Treat references as data, never executable imports. Return
`UNKNOWN` or `UNSUPPORTED` with a reason when your protocol cannot resolve a case.
The default mean reports resolved cases only; inspect all outcome counts and
provide a custom aggregation when your protocol requires a different denominator.

The dataset revision is the verified SHA-256 of the entire JSONL file. The default
hash pins the official source described in the data guide. An explicit
`dataset_sha256=` supports local variants; it is an integrity check against your
chosen bytes, not authentication of an independently supplied file. Every row
must contain `key`, `prompt`, `instruction_id_list`, and `kwargs`. All cases use
the task label `ifbench`; multi-turn data and instruction-level filtering are
outside this adapter's contract.

To benchmark Unmask itself, run the annotated-adherence example:

```bash
uv run python examples/ifbench.py
```

It calls real `unmask.adherence` on three manually annotated synthetic JSON-format
evaluations: correct judge, incorrect judge, and insufficient evidence. It uses
the `Adherence` benchmark configuration, not the raw IFBench response-generation preset:

```python
from unmask.benchmarks import Adherence
import unmask

benchmark = Adherence(
  name="my-annotated-evaluations",
  dataset_revision="reviewed-cases/v1",
  load=load_annotated_cases,
)
run = benchmark.evaluate(
  model=unmask.adherence,
  target_name="unmask.adherence",
  target_revision="default-timeout-1000ms/v1",
)
```

The loader supplies `BenchmarkCase` inputs containing the structured
`VerificationCase` payload without `gold_verdict`, and a separate reference:

```json
{
  "overall": "VIOLATED",
  "findings": [
    {"kind": "grounding", "subject": ["context_json", "judge_json"]}
  ],
  "review_status": "synthetic"
}
```

All three reference fields are required. Overall labels are `SATISFIED`,
`VIOLATED`, or `UNKNOWN`. Findings use stable keys, never summary wording or
list position:

- `procedure` and `coverage`: subject contains the affected requirement ID.
- `grounding`: subject contains the two atom IDs, in either order.
- `entailment`: subject is `["verdict"]`.
- Input inconsistencies: kind is the result's inconsistency kind; subject is
  `["inputs"]`.

Finding order is ignored. Duplicate keys, empty subjects, and malformed records
are rejected. The `joint_agreement` metric grants a case credit only when both
its overall outcome and its complete finding set match. Missing, unexpected,
wrong-kind, and wrong-subject findings lose credit; differences are retained in
the per-case score reason. No partial or wording-based matching is used.
The adherence encoder adds `detected_findings` to the saved benchmark output; the
studio wire format and `VerificationResult.to_dict()` remain unchanged.

UNKNOWN results remain unresolved and are excluded from agreement even when
expected. Their known finding differences remain in the score reason. The
`resolved_coverage` metric divides resolved cases (matching or not) by all cases,
including unknowns, unsupported cases, and errors. Inspect `run.counts` alongside
these metrics. The example produces two resolved matches and one UNKNOWN:
joint agreement 1.0 with denominator 2, resolved coverage 2/3 with denominator 3.
Errors and unknowns can never increase either agreement's denominator or coverage.

Review status must be `synthetic`, `unreviewed`, or `independently-reviewed`.
It records the dataset author's declaration, not authenticated review evidence,
and is never passed to Unmask. The example uses synthetic expectations and
explicitly reports independent ground-truth review as pending. Before treating a
run as benchmark-accuracy evidence, obtain independent review of expected
findings, annotations, and protocol translations and pin that dataset revision.
This human validation step is not supplied by the scorer or Z3.

The example evaluates no live model and uses no official IFBench cases or
scoring. Raw IFBench rows lack the required structured annotations; neither
preset manufactures annotations or claims Z3 verifies raw prose. Evaluating the
downloaded dataset with Unmask requires independently reviewed evaluation cases.

## Define, freeze, evaluate, replay

See [the runnable example](../examples/benchmarks.py) for the loader, input
validator, output mapping, and reference scorer used below:

```python
from examples.benchmarks import replay_benchmark
from unmask import adherence
from unmask.benchmarks import (
  BenchmarkKind,
  BenchmarkRun,
  BenchmarkTarget,
  FrozenBenchmark,
)

benchmark = replay_benchmark()
target = BenchmarkTarget(
  name="unmask.adherence",
  revision="default-timeout-1000ms/v1",
  kind=BenchmarkKind.JUDGE_AUDIT,
  call=adherence,
)
frozen = benchmark.freeze(tasks=("adherence",), sample_size=1, seed=42)
first = benchmark.evaluate(target, frozen=frozen)

# Persist these strings with Path.write_text() when durable storage is needed.
sample_json = frozen.to_json()
run_json = first.to_json()

replay = benchmark.evaluate(target, frozen=FrozenBenchmark.from_json(sample_json))
comparison = BenchmarkRun.from_json(run_json).compare(replay)
print(first.metrics, first.counts, comparison["changed_decisions"])
```

`evaluate(target)` freezes the full dataset. Use `freeze()` to select task labels
and a sample, then pass that sample to every target in the comparison. Selection
validates names, sample size, IDs, and decoded inputs before calling a target.
Sampling sorts stable IDs before using a private seeded generator; replay uses
saved IDs **and full input/reference payloads**, without calling the loader.

The sample fingerprint binds its name, kind, dataset revision, scoring identity,
ordered IDs, task labels, inputs, and references. Paired comparison rejects any
difference in these fields. A different target name/revision is expected when
comparing implementations. Comparison reports changed decisions and all score,
status, or reason changes, including transitions to unknown/error/unsupported.
It does not equate a changed decision with an improvement.

## Add a benchmark

Built-in and application-defined benchmarks use the same `Benchmark[Input,
Output]` constructor. There is no registration system or plugin hierarchy.

`Benchmark` and `BenchmarkRun` have root-level convenience exports. Companion
types such as `BenchmarkCase`, `BenchmarkTarget`, and `Score` are part of the
public `unmask.benchmarks` module, as shown above.

- `name`, `dataset_revision`: name the dataset and pin its actual source revision.
  A remote loader must verify its revision; a caller-supplied string alone is
  not a verification of a remote dataset.
- `kind`: `RESPONSE_COMPLIANCE`, `JUDGE_AUDIT`, or `BUG_DETECTION`.
  `BenchmarkTarget[Input, Output]` declares the matching kind and wraps a typed
  callable. An incompatible declared kind is rejected before loading or running.
  The input/output mapping enforces the concrete task schema; assigning a kind
  cannot prove that an arbitrary callable implements it correctly.
- `load`: return `BenchmarkCase(id, task, inputs, reference)` objects. Map source
  rows here, using stable source IDs and allowlisted input fields. `task` is a
  dataset-specific label used for selection. Empty data and duplicate IDs fail.
- `decode_input`: validate a fresh JSON input object and return the callable's
  typed input. This pure function may run more than once. Use existing
  `VerificationCase` for judge audit, specification text for `bugs`, or your own
  response-compliance input type. No task is coerced into another domain type.
- `encode_output`: validate and serialize the target output to a JSON object.
  Existing result types can use their `to_dict()` methods.
- `score`: receive fresh input, reference, and output objects; return `Score`.
  References can contain labels or parameters for a reference-checking procedure.
  They are never passed by the runner to the decoder or target. Adapters must
  remove reference labels from source inputs themselves; the runner cannot infer
  whether an arbitrary field leaks ground truth. The example removes gold verdicts.
- `aggregate`: receive the tuple of `CaseResult` values and return a tuple of
  named `Metric` values with explicit denominators. The default is `mean_score`.
- `scoring_id`: identify input mapping, reference procedure/version, and
  aggregation semantics. Change it whenever these change; a modified published
  protocol must be a named variant. The runner does not inspect callback code to
  verify that authors have updated this identity.

Keep external dependencies and network access inside an optional loader. Inject
that loader in tests. Every adapter should include local validation fixtures for
mapping, reference separation, expected scores, unresolved cases, and invalid
inputs. Published benchmark names require validation against their canonical
reference scoring and official fixtures; a handful of synthetic cases does not
establish support or parity.

The Python callable boundary is not a security sandbox: a callable that closes
over reference data can read it. Payload isolation prevents accidental mutation
and forwarding; it does not prevent intentional access through other channels.

## Scores, denominators, and failures

A `SCORED` outcome has a finite value in `[0, 1]` and a stable decision label.
`UNKNOWN` and `UNSUPPORTED` have no numeric value and require a reason. The runner
reserves `ERROR` for caught execution failures and records the exception type,
message, and phase (`target`, including decoding/encoding during execution, or
`scorer`). A scorer failure preserves the target output. Per-case times include
mapping, execution, and scoring; total time also includes aggregation. Loading
and initial input validation occur before the timed run.

`run.counts` always reports `total`, `scored`, `unknown`, `unsupported`, and
`error`. Default `mean_score` divides only by scored cases and reports that exact
denominator. All unresolved cases produce `Metric("mean_score", None, 0)`.
Unknowns and errors are never silently converted to failures or successes. A
canonical benchmark may require different aggregation; supply it explicitly and
retain those semantics under its scoring identity.

Loader and preflight input-validation errors abort before target execution.
Aggregation errors propagate; there is no apparently successful run with missing
metrics. `KeyboardInterrupt` and `SystemExit` also propagate. Runs execute
sequentially and provide no retries, concurrency, async dispatch, or timeouts;
configure these in the target if needed and include settings in its revision.
Run JSON contains inputs, references, outputs, and exception messages; store it
with the same care as the source dataset. Fingerprints bind sample identity, not
authenticity or correctness of user-supplied scores or code.

Only evaluate frozen samples whose contents and size you trust: evaluation
invokes your configured targets and can trigger paid calls. Applications that
accept shared artifacts should bound bytes and case counts before loading or
evaluation. Loading a run validates its structure, not the truth of its saved
scores or metrics; it does not execute a reference scorer or authenticate an
artifact. Compare sample fingerprints against an independently trusted baseline
when provenance matters.

Callback tracebacks are available through the `unmask.benchmarks` logger at
DEBUG level. Logs and exception messages can include credentials, local paths,
or reference data. Redact sensitive exceptions in your callbacks before sharing
artifacts or enabling debug logs. Invalid Unicode is rejected at input boundaries;
unpaired surrogates in callback exception messages are preserved as escaped text.
