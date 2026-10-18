"""Run and replay a local API demonstration; this is not a published benchmark."""

from importlib.resources import files

import unmask
from unmask.benchmarks import (
  Adherence,
  Benchmark,
  BenchmarkCase,
  BenchmarkKind,
  BenchmarkRun,
  BenchmarkTarget,
  FrozenBenchmark,
)


def load_replays() -> tuple[BenchmarkCase, ...]:
  text = (
    files("unmask").joinpath("web/samples/judge-audit.json").read_text(encoding="utf-8")
  )
  case = unmask.VerificationCase.from_json(text)
  inputs = case.to_dict()
  _ = inputs.pop("gold_verdict", None)
  return (
    BenchmarkCase(
      case.id,
      "adherence",
      inputs,
      {
        "overall": "SATISFIED",
        "findings": [],
        "review_status": "synthetic",
      },
    ),
  )


def replay_benchmark() -> Benchmark[unmask.VerificationCase, unmask.VerificationResult]:
  return Adherence(
    name="unmask-replay-example",
    dataset_revision="bundled-annotated-replay/v1",
    load=load_replays,
  )


def main() -> None:
  benchmark = replay_benchmark()
  target = BenchmarkTarget(
    "unmask.adherence",
    "default-timeout-1000ms/v1",
    BenchmarkKind.JUDGE_AUDIT,
    unmask.adherence,
  )
  frozen = benchmark.freeze(tasks=("adherence",))
  first = benchmark.evaluate(target, frozen=frozen)
  # These JSON strings can be saved to files and restored in another process.
  saved_sample = FrozenBenchmark.from_json(frozen.to_json())
  saved_run = BenchmarkRun.from_json(first.to_json())
  replay = benchmark.evaluate(target, frozen=saved_sample)
  print("Local API demonstration; not a published benchmark score.")
  print(first.metrics)
  print(first.counts)
  print("Changed decisions:", saved_run.compare(replay)["changed_decisions"])


if __name__ == "__main__":
  main()
