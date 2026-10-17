# Analysis studio

Start the studio with `uv run unmask serve` (see the [README](../README.md#commands)).

## Specifications, Scan and Diagnose

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

## Adherence in the studio

In the studio, choose **Adherence**, then **Open example**, import a JSON case,
or paste a complete case. **Sources** shows the verbatim protocol, context,
candidate response and observable judge rationale. **Annotations** shows the
supplied Boolean model, obligations, facts, evidence and provenance. The observed
PASS/FAIL verdict stays visible. Sources are read-only; **Replace case** validates
a replacement and invalidates the previous report.
Use **New case** to import another evaluation while keeping the saved case.

**Check adherence** reports observable coverage, procedural adherence, separate context
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

## Development checks

Run all Python tests with `uv run pytest` and lint/type checks with
`uv run prek run --all-files`. Replay UI behavior also has dependency-free Node
checks: `node --test tests/frontend/audit.test.cjs` (Node 22+). These exercise the
actual UI functions using a small DOM/storage harness; browser layout still
requires visual inspection. The ported core tests include 150 randomized Boolean
formulas checked against exhaustive truth tables.
