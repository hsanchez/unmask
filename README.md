# Unmask

Unmask finds defects in natural-language and executable regex guardrails.

It currently detects three defect classes:

1. **Over-strict rules** — a blocking rule can apply to a semantically safe request.
2. **Contradictions** — allow and block rules can apply to the same request.
3. **Ambiguities** — two plausible readings of a directive differ in applicability or decision.

For natural-language guardrails, an LLM translates the specification into shared Boolean predicates and disjunctive-normal-form conditions. Z3 then proves overlaps or interpretation divergence and returns predicate assignments as witnesses. These are proofs over the extracted symbolic model; faithfulness to the source language depends on the LLM formalization.

Regex analysis executes the supplied rules against trusted labeled examples or reported incidents. It preserves exact match spans, patterns, expected-outcome evidence, Z3 proofs, and analysis limits.

## What it does

| Task | Library | CLI | Studio |
| --- | --- | --- | --- |
| Find specification bugs | `unmask.bugs` | `unmask scan` | **Scan** |
| Diagnose reported incidents (regex) | `unmask.service.diagnose_specification` | `unmask diagnose` | **Diagnose** |
| Check judge protocol adherence | `unmask.adherence` | `unmask adherence` | **Adherence** |

`scan` returns *bugs*: over-strict rules, contradictions and ambiguities.
`adherence` checks whether an annotated judge evaluation followed its protocol;
it needs no model provider.

## Quickstart

Python 3.14 or newer and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync
uv run unmask scan examples/regexes.yaml --taxonomy examples/taxonomy.yaml
uv run unmask serve
```

The regex scan runs locally and needs no credentials. It writes `output/scan/scan.json`
and reports three findings. `serve` opens the local studio in your browser.
Natural-language scans need a model provider; see [Provider setup](#provider-setup).

## Python library

The library, CLI, and studio share the same analysis. Library calls accept
in-memory inputs and do not require a running web server.

Runnable scripts and commands are in [examples/README.md](examples/README.md).

### Find specification bugs

Regex specifications run locally, without provider credentials:

```python
from pathlib import Path
import unmask

result = unmask.bugs(Path("regexes.yaml").read_text())
for finding in result.findings:
  print(finding.summary)
```

Natural-language specifications require a configured extraction model and its
provider extra. `model` accepts the existing `TargetConfig`:

```python
model = unmask.TargetConfig(
  provider="openai",
  judge_model="gpt-4o",
  project=None,
  location="global",
  thinking_budget=None,
)
result = unmask.bugs(
  Path("guardrail.yaml").read_text(),
  model=model,
  # taxonomy_text=Path("taxonomy.yaml").read_text(),
)
```

Provider credentials come from the process environment. Library imports and
calls do not load `.env` files. Applications can inject a backend through
`backend_factory` for testing or integration; it follows the existing
`unmask.backend.JudgeBackend` protocol. Findings include severity, rationale,
witnesses, rule IDs, and source references. `result.to_dict()` retains the
studio's scan JSON format.

Natural-language analysis sends specification and taxonomy content to the selected
provider and may incur charges. Treat extracted interpretations as model output,
not trusted ground truth; the solver's conclusions depend on that interpretation.

### Assess protocol adherence

Use the same annotated replay JSON accepted by the studio's **Adherence**
workflow:

```python
case_json = Path("annotated-case.json").read_text()
case = unmask.VerificationCase.from_json(case_json)
result = unmask.adherence(case)

print(result.status, result.overall)
for finding in result.findings:
  print(finding.summary)
for check in result.checks:
  print(check.dimension, check.status)
```

`from_json` accepts a string containing JSON. It validates the protocol's atoms,
constraints, requirements and decision expression, context, candidate response,
judge rationale/verdict, annotated facts and exact evidence spans, grounding
pairs, and extraction metadata. A gold verdict is optional. The packaged
`unmask/web/samples/judge-audit.json` is a complete example.

Direct Python construction uses the same invariants and typed inputs:

```python
case = unmask.VerificationCase(
  id="example",
  protocol_text="PASS iff the context supports the response.",
  context="Alice is CTO.",
  response="Alice is CTO.",
  rationale="Supported. PASS.",
  verdict="PASS",
  atoms=(unmask.Atom("supported", "judge", "Judge states support"),),
  decision=unmask.Expr("atom", atom="supported"),
  annotations=(
    unmask.Annotation(
      "supported",
      unmask.Truth.TRUE,
      (unmask.Evidence("rationale", 0, 10, "Supported."),),
      "Judge states support.",
    ),
  ),
  extraction={"provider": "manual", "translation_status": "VALID"},
)
result = unmask.adherence(case)
```

This minimal case establishes verdict entailment but leaves overall adherence
`UNKNOWN`: no procedural requirements or independent grounding pairs were
supplied. `annotations` are supplied observations; `result.findings` are
detected violations or inconsistencies. `result.checks` includes successful and
inconclusive checks, and `result.report` retains the detailed replay report
(whose legacy JSON `findings` field denotes annotations).

Adherence runs locally without a model provider. Z3 verifies the supplied
structured interpretation, not raw prose or hidden reasoning. Empty findings
do not imply success: always inspect `result.overall`, `result.status`, and
`result.limitations`. `timeout_ms` bounds each solver query within the existing
total audit budget. `VerificationCase.to_dict()` exports replay input data.
`result.to_dict()` returns the studio's JSON report for the same check (`summary`,
`checks`, `annotations`, `unknownAtoms`, `limitations` and the replay envelope).

Invalid replay input raises `unmask.ValidationError` (a `ValueError`);
malformed specification inputs raise `TypeError`, `ValueError`, or a PyYAML
error. Provider/execution failures raise `RuntimeError`. Solver uncertainty is
reported explicitly rather than treated as adherence. JSON and direct case
construction both validate invariants; there is no unstructured `from_text`
constructor or automatic annotation extraction.

## Provider setup

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

Studio workflows (Scan, Diagnose, Adherence, proposed fixes) are described in [docs/studio.md](docs/studio.md).

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

### Check protocol adherence

```bash
uv run unmask adherence case.json --output report.json
```

`adherence` checks a supplied structured replay locally without a model provider.
Omit `--output` to print the report as JSON. A completed check returns exit code 0,
including uncertain or inconsistent results; invalid inputs return 1. Argument
errors return 2. Completion does not mean the judge followed its protocol.

The studio's **Adherence** workflow and the replay JSON format are described in [docs/studio.md](docs/studio.md#adherence-in-the-studio) and [docs/schemas.md](docs/schemas.md#judge-replay).

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

## Exit codes

- `0`: analysis completed, including results with findings or insufficient evidence.
- `1`: input, model, filesystem, or analysis failure. Input errors name the file, with line and column for YAML syntax errors.
- `2`: invalid command-line arguments reported by `argparse`.

## More documentation

- [docs/studio.md](docs/studio.md): studio workflows, proposed fixes, adherence, development checks.
- [docs/schemas.md](docs/schemas.md): input schemas, symbolic semantics and the judge replay format.
- [examples/README.md](examples/README.md): runnable Python and CLI examples.
- [docs/decisions/](docs/decisions/): architecture decision records.
