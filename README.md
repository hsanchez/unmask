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

Regex scanning and diagnosis work out of the box. Natural-language scanning needs an LLM
judge backend, selected with `--provider` (default `gemini`) and installed as an optional
extra:

```bash
uv sync --extra gemini      # Gemini (Vertex AI or the Gemini Developer API)
uv sync --extra openai      # OpenAI
uv sync --extra anthropic   # Anthropic
```

Each provider reads its credentials from the environment. Copy `.env.example` to `.env`
and fill in the values you need — `unmask` loads it automatically, searching upward from
the current directory (not from wherever `unmask` itself is installed). Pass `--env-file
PATH` to `scan`/`diagnose` to load a specific file instead:

```bash
cp .env.example .env
```

| Provider | Credentials |
| --- | --- |
| `gemini` | `GOOGLE_CLOUD_PROJECT` (+ optional `GOOGLE_CLOUD_LOCATION`, default `global`) for Vertex AI, **or** `GOOGLE_API_KEY`/`GEMINI_API_KEY` for the Gemini Developer API |
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |

For Gemini, setting `GOOGLE_CLOUD_PROJECT` selects Vertex AI; leaving it unset and setting
`GOOGLE_API_KEY` instead selects the Gemini Developer API. Credential resolution for both
modes is handled by the `google-genai` SDK.

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
  --provider gemini \
  --project your-project \
  --location global \
  --model gemini-2.5-flash \
  --thinking-budget 128
```

`--provider` selects the judge backend (`gemini`, `openai`, or `anthropic`); `--model`
defaults to a provider-specific model when not set (`gemini-2.5-flash`, `gpt-4o`, and
`claude-opus-4-5`, respectively). `--project`, `--location`, and `--thinking-budget` only
apply to the `gemini` provider.

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

## Example defect catalog

The files in `examples/` form a compact specification-defect catalog. They are
designed to explain Unmask's supported findings rather than model a complete
production financial policy.

| Defect | Canonical case | Evidence |
| --- | --- | --- |
| Over-strictness | `Anthropic will invest up to $2 billion.` | A broad block rule matches `invest`, although neutral third-party reporting is expected to be allowed. |
| Contradiction | `Tell me exactly how to trade Acme shares today.` | The same `trade` span matches an allow rule and a block rule. |
| Ambiguity | `I am a 30-year-old first-time investor. What should I buy?` | The either-protected-audience interpretation applies, while the both-protected-audiences interpretation does not. |

`examples/regexes.yaml` contains five manifest examples, including allowed and
blocked controls. `examples/taxonomy.yaml` contributes a sixth, distinct regex
example. A regex scan therefore reports three findings across six cases, one for
each defect class. Diagnosing `examples/violations.json` produces three incident
reports with one focused finding per incident.

Regex scan and diagnosis run locally:

```bash
uv run unmask scan examples/regexes.yaml \
  --taxonomy examples/taxonomy.yaml \
  --output output/regex-scan

uv run unmask diagnose examples/regexes.yaml \
  examples/violations.json \
  --output output/diagnose \
  --open
```

Scanning `examples/guardrail.yaml` exercises the same concepts through neural
semantic extraction and requires the Vertex AI setup described above.

## Input schemas

### Natural-language guardrail

```yaml
name: financial-information-policy
system_prompt: >-
  Provide accurate financial education and summaries of public company
  announcements without giving personalized investment advice.
rules:
  - Block any response containing the words buy, sell, or invest.
  - Allow general financial education that does not recommend a specific transaction.
  - Block instructions explaining how to trade a specific investment.
  - Block personalized investment recommendations for minors and first-time investors.
examples:
  - content: Anthropic will invest up to $2 billion.
    should_block: false
    reasoning: Neutral reporting about a third-party company action is allowed.
```

Every rule is a non-empty string. Every example requires non-empty `content` and Boolean `should_block`.

### Regex manifest

```yaml
schema_version: 1
kind: regex
name: financial-information-policy
engine: python_re
scope: agent_output
match_timeout_seconds: 1.0

rules:
  - id: block-investment-term
    action: block
    pattern: '\binvest\b'
    flags: [IGNORECASE]
    description: Block any use of the word invest.
  - id: first-time-investor-scope
    action: block
    pattern: '\A(?=.*\bfirst-time investor\b)(?=.*\bbuy\b).*\Z'
    flags: [IGNORECASE, DOTALL]
    description: Block personalized recommendations for minors and first-time investors.
    interpretations:
      - label: either protected audience
        action: block
        pattern: '\A(?=.*\b(?:minor|first-time investor)\b)(?=.*\bbuy\b).*\Z'
      - label: both protected audiences
        action: block
        pattern: '\A(?=.*\bminor\b)(?=.*\bfirst-time investor\b)(?=.*\bbuy\b).*\Z'

examples:
  - content: Anthropic will invest up to $2 billion.
    expected_action: allow
    reason: Neutral reporting about a third-party company action is allowed.
```

Regex ambiguity analysis requires exactly two explicit `interpretations` on a rule. Regex contradiction analysis requires both allow and block rules. Over-strictness requires a trusted expected `allow` action and policy reason.

### Violations

```json
{
  "violations": [
    {
      "context": "Anthropic will invest up to $2 billion.",
      "location": 1842,
      "value": "invest",
      "expected_outcome": "allow",
      "expected_reason": "Neutral reporting about a third-party company action is allowed."
    }
  ]
}
```

`location` is a non-negative source offset. `value` must occur exactly once in `context`. `expected_outcome` and `expected_reason` are optional but must be supplied together.

### Taxonomy

Natural-language scans pass the complete YAML document to the semantic extractor as context:

```yaml
intent_classes:
  NeutralThirdPartyReporting:
    expected_action: allow
    description: Factual reporting about an action taken by a named third party.
  GeneralFinancialEducation:
    expected_action: allow
    description: Explanations of financial concepts without a recommendation to transact.
  PersonalizedInvestmentRecommendation:
    expected_action: block
    description: Advice directing a person to transact in a specific asset.
```

Regex scans consume versioned labeled examples from the same optional file:

```yaml
regex_examples_version: 1
regex_examples:
  - content: Summarize how index funds spread investment risk.
    expected_action: allow
    reason: This is general financial education and does not recommend a transaction.
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
