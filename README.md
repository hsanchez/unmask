# Unmask

Unmask finds defects in natural-language and executable regex guardrails.

It currently detects three defect classes:

1. **Over-strict rules** — a blocking rule can apply to a semantically safe request.
2. **Contradictions** — allow and block rules can apply to the same request.
3. **Ambiguities** — two plausible readings of a directive differ in applicability or decision.

For natural-language guardrails, an LLM translates the specification into shared Boolean predicates and disjunctive-normal-form conditions. Z3 then proves overlaps or interpretation divergence and returns predicate assignments as witnesses. These are proofs over the extracted symbolic model; faithfulness to the source language depends on the LLM formalization.

Regex analysis executes the supplied rules against trusted labeled examples or reported incidents. It preserves exact match spans, patterns, expected-outcome evidence, Z3 proofs, and analysis limits.

## Install

Python 3.14 or newer is required.

```bash
uv sync --extra gemini
```

Natural-language scanning uses Vertex AI. Set the project before scanning:

```bash
export GOOGLE_CLOUD_PROJECT=your-project
```

`GOOGLE_CLOUD_LOCATION` is optional and defaults to `global`.

## Commands

### Scan a specification

```bash
uv run unmask scan examples/guardrail.yaml \
  --taxonomy examples/taxonomy.yaml \
  --output output/scan
```

Natural-language scans write:

```text
output/scan/
  symbolic_model.json
  findings.json
```

Model settings can be overridden directly:

```bash
uv run unmask scan examples/guardrail.yaml \
  --project your-project \
  --location global \
  --model gemini-2.5-flash \
  --thinking-budget 128
```

The same command recognizes a regex manifest through `kind: regex`:

```bash
uv run unmask scan examples/regexes.yaml \
  --taxonomy examples/taxonomy.yaml \
  --output output/regex-scan
```

Regex scans write `scan.json`. In v0.1, they batch-check labeled examples from the manifest and taxonomy. A manifest without usable examples returns `insufficient_evidence`. Candidate-string generation, semantic label inference, and alternate-interpretation inference are outside the v0.1 scan contract.

### Diagnose reported incidents

```bash
uv run unmask diagnose examples/regexes.yaml \
  examples/violations.json \
  --output output/diagnose \
  --open
```

Diagnosis writes one JSON and HTML report per incident plus a collection index:

```text
output/diagnose/
  index.html
  incidents/0001/report.json
  incidents/0001/report.html
```

`--open` opens `index.html` after artifact generation. A browser failure produces a warning while preserving a successful diagnosis result.

Existing files at deterministic artifact paths are overwritten. Unmask leaves unrelated and stale files in the output directory untouched.

## Input schemas

### Natural-language guardrail

```yaml
name: investment-claims
system_prompt: >-
  Give accurate summaries of company announcements.
rules:
  - Never recommend that the user buy, sell, or invest.
examples:
  - content: Anthropic will invest up to $2 billion.
    should_block: false
    reasoning: This reports a third-party action.
```

Every rule is a non-empty string. Every example requires non-empty `content` and Boolean `should_block`.

### Regex manifest

```yaml
schema_version: 1
kind: regex
name: investment-claims
engine: python_re
scope: agent_output
match_timeout_seconds: 1.0

rules:
  - id: investment
    action: block
    pattern: '\b(?:buy|sell|invest)\b'
    flags: [IGNORECASE]
    description: Detect investment language.

examples:
  - content: Anthropic will invest up to $2 billion.
    expected_action: allow
    reason: This reports a third-party action.
```

Regex ambiguity analysis requires exactly two explicit `interpretations` on a rule. Regex contradiction analysis requires both allow and block rules. Over-strictness requires a trusted expected `allow` action and policy reason.

### Violations

```json
{
  "violations": [
    {
      "context": "Anthropic will invest up to $2 billion.",
      "location": 1098,
      "value": "invest",
      "expected_outcome": "allow",
      "expected_reason": "This reports a third-party action."
    }
  ]
}
```

`location` is a non-negative source offset. `value` must occur exactly once in `context`. `expected_outcome` and `expected_reason` are optional but must be supplied together.

### Taxonomy

Natural-language scans pass the complete YAML document to the semantic extractor as context:

```yaml
intent_classes:
  Allowed:
    description: Neutral reporting about third-party actions.
  Blocked:
    description: Direct personalized investment recommendations.
```

Regex scans consume versioned labeled examples from the same optional file:

```yaml
regex_examples_version: 1
regex_examples:
  - content: Anthropic will invest up to $2 billion.
    expected_action: allow
    reason: This reports a third-party action.
```

Both sections may coexist in one taxonomy document.

## Symbolic semantics

Conditions use disjunctive normal form: the outer list is OR and each inner list is AND.

- Over-strictness: `safe(x) AND blocked(x)`
- Contradiction: `allowed(x) AND blocked(x)`
- Same-decision ambiguity: `XOR(interpretationA(x), interpretationB(x))`
- Opposite-decision ambiguity: `interpretationA(x) OR interpretationB(x)`

Natural-language ambiguity findings retain the source excerpt, ambiguity rationale, both interpretation labels and decisions, and both interpretation rationales. The accompanying finding rationale explains the Z3 proof.

## Exit codes

- `0`: analysis completed, including results with findings or insufficient evidence.
- `1`: input, model, filesystem, or analysis failure.
- `2`: invalid command-line arguments reported by `argparse`.
