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

### Open the local analysis studio

```bash
uv run unmask serve
```

`serve` starts a Flask server on `http://127.0.0.1:8765/` and opens the studio in
the default browser. It serves bundled HTML, CSS, and plain browser JavaScript; no
frontend build step or Node runtime is required. Use an available port or keep the
browser closed when starting the server:

```bash
uv run unmask serve --port 0
uv run unmask serve --no-open
```

`--env-file PATH` selects a credential file using the same allowlisted environment
loading behavior as the scan commands.

The studio starts empty. Load a `.yaml` or `.yml` specification, or open a bundled
example from the Specifications panel. Use the Analysis panel to choose **Scan** or
**Diagnose**, configure the workflow, and run it. Imported studies and completed
results stay in the browser's IndexedDB storage. Rename and delete work for both
bundled examples and imported specifications; removed bundled examples can be
restored from the Specifications panel. These actions affect browser-local storage
only; Unmask does not retain uploaded files on the server.

**Scan** runs the same analysis as `unmask scan`. Regex scans run locally.
Natural-language scans send the specification and optional taxonomy to the selected
configured model provider; Volume controls the model thinking budget. **Diagnose**
is available for regex specifications and needs a violations JSON file; the bundled
regex example includes one. Credentials remain in the server process environment
and are never sent to or stored by the browser.

Source highlighting initially maps findings to complete YAML rule values. Hover,
focus, or select either a source rule or finding card to reveal its relationships in
the opposite pane.

Each supported finding can request a **Proposed fix** from the selected provider.
The provider returns replacements for the affected rules only. Unmask constructs the
complete candidate specification, validates its YAML and rule syntax, reruns the
analysis, checks every supplied labeled example, and rejects candidates that retain
the target finding or introduce a new supported finding. Only candidates that pass
these bounded checks appear as a copyable unified diff; the studio does not apply or
write the change.

Proposed-fix generation is an explicit external-model action. For regex policies,
Scan and Diagnose remain local, while generating a fix sends the affected rules,
finding evidence, labeled examples, optional taxonomy, and attached incidents to the
selected provider. Z3 verifies consistency and witness conditions after the candidate
is built; it does not author the replacement.

Natural-language fixes require trusted labeled examples and two stable semantic
extractions and example evaluations. Inline `examples` qualify. A taxonomy can add
cases with this versioned shape:

```yaml
policy_examples_version: 1
policy_examples:
  - content: Anthropic will invest up to $2 billion.
    should_block: false
    reasoning: Neutral third-party reporting is allowed.
```

Z3 proves properties of the newly extracted Boolean model, not of prose directly.
When evidence is missing, extraction is unstable, labels regress, or verification
fails, the studio reports that no sufficiently supported automatic fix is available.

Specification, taxonomy, incident, and example text is untrusted model input. Unmask
delimits it in repair prompts and never executes instructions from it, but that text
can still influence the proposed edit. Supplied example labels are the regression
oracle, not a general proof of real-world policy coverage. Review every diff before
using it and only attach taxonomy and incident files from sources you trust.

Repair evaluation hides inline example labels from the classifier. Provider calls
use a 60-second timeout with automatic SDK retries disabled; a repair stops starting
new model calls after five minutes. The single repair slot is per server process.
Cancelling in the browser stops waiting; an in-flight server call runs until it
finishes or times out. Custom judge backends return `JudgeResponse(text, usage)`.

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

### Audit an existing judge evaluation

```bash
uv run unmask audit-judge case.json --output report.json
```

`audit-judge` audits a supplied structured replay locally without a model provider.
Omit `--output` to print the report as JSON. A completed audit returns exit code 0,
including uncertain or inconsistent results; invalid inputs return 1. Argument
errors return 2. Completion does not mean the judge passed the audit.

In the studio, choose **Audit judge**, then **Open example**, import a JSON case,
or paste a complete case. **Sources** shows the verbatim protocol, context,
candidate response and observable judge rationale. **Annotations** shows the
supplied Boolean model, obligations, facts, evidence and provenance. The observed
PASS/FAIL verdict stays visible. Sources are read-only; **Replace case** validates
a replacement and invalidates the previous report.
Use **New case** to import another evaluation while keeping the saved case.

**Run audit** reports observable coverage, procedural adherence, separate context
grounding, rationale/verdict compatibility and verdict entailment. Evidence links
open the appropriate source and highlight the exact quoted span. Solver details
include possible assignments and conflicting assertions. Missing evidence and
unknown semantic facts remain unknown. A compatible verdict can still be
underdetermined; an inconsistent base never proves a verdict.

Audits apply only to the supplied formalization and observable evidence. A span
check verifies a quotation, not its semantic interpretation. Translation status
is asserted by the annotator. No live natural-language extraction or hidden
reasoning analysis is performed. Optional gold labels are displayed separately
and never constrain the solver.

Cases and completed reports stay in browser-local storage. **Export case** saves
a replay JSON; **Export report** saves the report with its case, content hash and
auditor/schema versions. Importing a report loads its case and requires a fresh
audit. Cached results are discarded when their case or auditor version changes.

The version-1 replay schema is demonstrated by
[`judge-audit.json`](src/unmask/web/samples/judge-audit.json). It includes
`schema_version`, `id`, `context`, `response`, `rationale`, `verdict`, `protocol`,
`findings`, `extraction`, optional `grounding_pairs` and optional `gold_verdict`.
Atoms have judge, context or procedure roles; expressions support Boolean
constants, atoms, and/or/not/implies/iff. TRUE/FALSE findings need exact evidence
spans; UNKNOWN findings may omit evidence. Evidence offsets use Unicode code
points and half-open ranges in the original source. The parser rejects unknown
fields, duplicate JSON keys, undeclared atoms and incompatible evidence roles.
Limits: 1 MB case input in the CLI, 128 atoms, 1,024 expression nodes and depth 16.
The web API caps the complete JSON request at 1 MB, including its wrapper and
escaping, so its effective case limit is lower. Each audit
uses a separate Z3 context, a five-second solver budget and one-second query
limits. Exhausted budgets produce solver-unknown results.
The server accepts one audit at a time per process. Cancelling stops the browser
from waiting; the current solver run finishes within its budget before releasing
the slot.

Run all Python tests with `uv run pytest` and lint/type checks with
`uv run prek run --all-files`. Replay UI behavior also has dependency-free Node
checks: `node --test tests/frontend/audit.test.cjs` (Node 22+). These exercise the
actual UI functions using a small DOM/storage harness; browser layout still
requires visual inspection. The ported core tests include 150 randomized Boolean
formulas checked against exhaustive truth tables.
