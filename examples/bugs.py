"""Find regex specification bugs locally, without a model provider."""

from pathlib import Path

import unmask


def main() -> None:
  examples = Path(__file__).resolve().parent
  result = unmask.bugs(
    (examples / "regexes.yaml").read_text(encoding="utf-8"),
    specification_name="regexes.yaml",
    taxonomy_text=(examples / "taxonomy.yaml").read_text(encoding="utf-8"),
  )
  print(f"Regex specification: {len(result.findings)} finding(s)")
  for finding in result.findings:
    print(f"{finding.bug_type}: {finding.summary}")


if __name__ == "__main__":
  main()
