"""Find natural-language specification bugs using a configured provider."""

import os
from pathlib import Path

import unmask


def main() -> None:
  examples = Path(__file__).resolve().parent
  model = unmask.TargetConfig(
    provider="openai",
    judge_model="gpt-4o",
    project=os.getenv("GOOGLE_CLOUD_PROJECT"),
    location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
    thinking_budget=None,
  )
  result = unmask.bugs(
    (examples / "guardrail.yaml").read_text(encoding="utf-8"),
    specification_name="guardrail.yaml",
    taxonomy_text=(examples / "taxonomy.yaml").read_text(encoding="utf-8"),
    model=model,
  )
  print(f"Natural-language specification: {len(result.findings)} finding(s)")
  for finding in result.findings:
    print(f"{finding.bug_type}: {finding.summary}")


if __name__ == "__main__":
  main()
