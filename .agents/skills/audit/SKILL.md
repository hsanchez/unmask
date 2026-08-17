---
name: audit
description: Audit code changes using either a standard review or a multi-model adversarial review.
---

# Audit Diff

Audit code changes.

`review-code` is the custom script that constructs the canonical review prompt. Reviewer backends are external command-line tools that consume that prompt.

## When to Use

- Reviewing uncommitted code changes.
- Checking changes before committing or opening a pull request.
- Getting independent review signal from multiple evaluative lenses.
- Filtering review noise through cross-persona confidence tiers.

## When NOT to Use

- `review-code` is unavailable.
- No reviewable changes exist.
- Auditing non-code files only.
- The user wants the current model's own inline review; just ask directly.
- The user wants several models to answer the same open-ended question; use the `council` skill for that.

## Audit Mode

If the user specifies `standard`, run Standard Audit.
If the user specifies `adversarial`, run Adversarial Audit.

If the user does not specify a mode, ask once:

Audit mode?
- Standard audit
- Adversarial audit

# Core Invariants

## Single Prompt Invariant

Run `review-code` exactly once per audit.

Every reviewer must review the exact same prompt produced by that single `review-code` invocation.

Do not re-run `review-code` per reviewer.

## Reviewer Independence Invariant

Reviewers must not see each other's outputs.

Each reviewer receives only the canonical review prompt plus its persona instructions.

Do not run iterative, conversational, or cross-informed review unless the user explicitly asks for that mode.

## Stable Interface Invariant

`review-code` defines the review protocol.

The audit skill coordinates execution, finding extraction, clustering, confidence-tier grouping, cleanup, and reporting.

# Standard Audit

Run from the repository root.

By default this audits uncommitted changes. If the user names a base ref (a branch, tag, or commit to compare against — for example "audit this branch against main"), pass it to `review-code` to audit committed changes relative to that ref, plus any uncommitted changes on top.

1. Check for untracked files:

   ```bash
   git ls-files --others --exclude-standard
   ```

   If any exist, inform the user that untracked files are omitted by
   `review-code` and `git diff`.

   Suggest:

   ```bash
   git add -N <file>
   ```

   Then stop and let the user re-invoke after staging with intent-to-add.

2. Run `review-code` once:

   ```bash
   REVIEW_CODE=scripts/review-code
   command -v "$REVIEW_CODE" >/dev/null 2>&1 || REVIEW_CODE=review-code
   "$REVIEW_CODE" ${BASE_REF:+"$BASE_REF"}
   ```

   `review-code` exits nonzero on a real failure (for example, an invalid base ref). Check the exit code from this command, not just its printed output — a nonzero exit means there is no usable prompt, regardless of what was printed. Stop and relay the error in that case.

3. Treat the output as a review prompt.

   `review-code` produces the prompt. The current model consumes that prompt and produces the actual audit findings.

4. Present findings with file and line references where possible.

5. Stop.

# Adversarial Audit

Run multiple reviewer personas against the same canonical review prompt. Each persona has a different review lens, and each can run on a different backend. Agreement across personas carries stronger signal because they are looking for different classes of failure.

By default this audits uncommitted changes. If the user names a base ref (a branch, tag, or commit to compare against), pass it through as `$BASE_REF` — `review-code` will then audit committed changes relative to that ref, plus any uncommitted changes on top.

## Personas

| Persona    | Focus                                                     | Default backend                       | Persona delivery          | Prompt file                                      |
|------------|-----------------------------------------------------------|---------------------------------------|---------------------------|--------------------------------------------------|
| Auditor    | Correctness, completeness, contract adherence, API misuse | `codex`                               | System/instruction prompt | `.agents/skills/audit/prompts/auditor.txt`       |
| Adversary  | Security, exploitability, high-impact failure modes       | `claude`                              | System prompt             | `.agents/skills/audit/prompts/adversary.txt`     |
| Pragmatist | Maintainability, test coverage, operational cost          | `copilot-gemini` (`gemini-3.5-flash`) | Combined user prompt      | `.agents/skills/audit/prompts/pragmatic.txt`     |

Default: run all three personas on their default backends.

Require at least 2 personas.

## Backends

Overrides are limited to these documented backends. Each backend has a fixed persona-delivery mechanism:

| Backend          | CLI          | Persona delivery                                    |
|------------------|--------------|------------------------------------------------------|
| `claude`         | `claude`     | System prompt (`--system-prompt`)                    |
| `codex`          | `codex`      | Instruction prompt (positional argument to `codex exec`) |
| `copilot-gemini` | `gh copilot` extension, or standalone `copilot` binary | Combined prompt (no separate system-prompt channel) |
| `agy`            | `agy`        | Combined prompt (positional argument to `agy --print`) |

Any persona can run on any backend in this table. A persona is not tied to its default backend.

If `gh copilot` is missing, fall back to the standalone `copilot` binary (same flags, no `gh copilot --` wrapper); if neither is present, treat it as missing per [Error Handling](#error-handling) — do not auto-install.

If `CODEX_SANDBOX` or `CODEX_THREAD_ID` is set, the audit is already running inside Codex. Treat `codex` as unavailable for this run. This is a confirmed sandbox limitation, not a precautionary assumption: a nested `codex exec` fails before running any review, with `Error: failed to initialize in-process app-server client: Operation not permitted (os error 1)` — the child process inherits the parent Seatbelt sandbox regardless of working directory, session, or environment-variable changes, so no wrapper (`bash`, `env -u`, `cd`, `nohup`, `setsid`) escapes it. Reroute Codex personas to `claude`. Only fall back to `agy` if `claude` is also unavailable or fails — `agy` is unreliable for this use case (see [Error Handling](#error-handling)) and should never be the first fallback tried. Do not retry nested Codex from another working directory or via a subprocess wrapper.

Default Claude reviews use the user's Claude login/OAuth session. Run them with `ANTHROPIC_API_KEY` removed from the subprocess environment; an inherited API key otherwise takes precedence over login auth. If the user explicitly requests API-key authentication, honor that override.

If a Copilot invocation fails because its quota or token allowance is exhausted, reroute that persona to `codex`, then `claude`, then `agy` only as a last resort (the full order and rationale are in [Copilot Prompt Limit and Fallback](#copilot-prompt-limit-and-fallback) and the Error Handling table). Detect quota exhaustion from the Copilot exit status and matching quota/exhaustion text in its stderr or stdout; do not treat a successful invocation with empty stdout as quota exhaustion. Record whichever backend actually ran the persona in the summary. Treat the persona as failed only if every fallback is unavailable or fails.

If a Copilot backend cannot accept the combined prompt because the prompt is too large, reroute that persona to `claude` when nested inside Codex (where `codex` is unavailable); otherwise use `codex`. This is a backend fallback, not a persona failure. If the selected fallback is unavailable or fails to start, try the other of `codex`/`claude`; only fall back to `agy` if both have failed. Only mark the persona failed after every fallback has failed.

If the user asks for a backend not in this table, tell them it is unsupported and list the table above.

## Execution Environment

Adversarial audit behaves differently depending on what launched it:

- **External orchestration** — launched from a normal terminal, a Zed task, or any other process that is not itself Codex. `codex exec` runs as an independent subprocess and receives only the canonical review prompt and its persona instructions, matching the Reviewer Independence Invariant exactly. Use this when independent Codex, Claude, and Copilot reviewers are all required.
- **Orchestration from inside Codex** — `CODEX_SANDBOX` or `CODEX_THREAD_ID` is set. Nested `codex exec` is unavailable (see [Backends](#backends)); Codex personas reroute per the documented fallback order. This reduces model diversity for those personas — report both the requested and effective backend in the summary (see [Summary](#summary)) so the reduction is visible, not silent.

## Gather Context

Ask the user for any parameters not already provided. Ask once.

**Question 1 - Personas**

Skip if personas were already specified.

Which personas should I run?

- All three: Auditor, Adversary, Pragmatist
- Custom: user specifies 2 or more personas

If the user picks fewer than 2 personas, ask them to add another.

**Question 2 - Backend Overrides**

Skip unless the user explicitly asks to change backends.

Which backend should each persona use? Defaults:

- Auditor: `codex`
- Adversary: `claude`
- Pragmatist: `copilot-gemini`

Only accept backends listed in [Backends](#backends). If the user names an unsupported backend, tell them so and ask again.

**Question 3 - Verbosity**

Skip if already specified.

How much output should I show?

- Confidence-tier findings only
- Confidence-tier findings plus disputed and solo findings
- Full verbose output with per-persona details

Default: confidence-tier findings only.

## Diff Preview

Always check for untracked files first:

```bash
git ls-files --others --exclude-standard
```

If any exist, stop and tell the user:

`review-code` and `git diff` ignore untracked files. These files would be silently omitted from the audit.

Suggest:

```bash
git add -N <file>
```

Then let the user re-invoke the audit.

Next show diff stats:

```bash
if [ -n "${BASE_REF:-}" ]; then
  git diff --stat --no-ext-diff "${BASE_REF}...HEAD"
fi
git diff --stat HEAD
```

If both are empty, stop and tell the user there are no changes to audit.

If the diff exceeds 2000 changed lines, warn the user and ask whether to proceed or narrow the scope.

## Review Input

Create a per-run scratch directory and generate the canonical review prompt. Resolve `$OUTPUT_DIR`, `$PROMPT_FILE`, and `$PERSONA_DIR` to absolute paths immediately — reviewer processes run from a neutral working directory (see [Reviewer Working Directory](#reviewer-working-directory)), so relative paths would break:

```bash
mkdir -p .scratch
OUTPUT_DIR=$(mktemp -d .scratch/audit-outputs.XXXXXX)
OUTPUT_DIR=$(cd "$OUTPUT_DIR" && pwd)
PROMPT_FILE=$(mktemp .scratch/review-prompt.XXXXXX)
PROMPT_FILE="$(cd "$(dirname "$PROMPT_FILE")" && pwd)/$(basename "$PROMPT_FILE")"
PERSONA_DIR="$(cd .agents/skills/audit/prompts && pwd)"
REVIEWER_CWD=$(mktemp -d)
trap 'rm -f "${PROMPT_FILE:-}"; rm -rf "${OUTPUT_DIR:-}" "${REVIEWER_CWD:-}"' EXIT

REVIEW_CODE=scripts/review-code
command -v "$REVIEW_CODE" >/dev/null 2>&1 || REVIEW_CODE=review-code
"$REVIEW_CODE" ${BASE_REF:+"$BASE_REF"} > "$PROMPT_FILE"
REVIEW_CODE_EXIT=$?
```

The `trap` is the authoritative cleanup mechanism for this run — it fires on normal completion, error, or interruption. The explicit cleanup step in [Cleanup](#cleanup) is a fallback for a session that cannot rely on shell traps, not a replacement.

Check the exit code before trusting `$PROMPT_FILE`. `review-code` exits nonzero on a real failure — for example an invalid `$BASE_REF` — and in that case `$PROMPT_FILE` does not contain a usable prompt:

```bash
if [ "$REVIEW_CODE_EXIT" -ne 0 ]; then
  # stop and relay the error review-code printed; do not proceed to execution
fi
```

Only once `review-code` has exited 0 should you check for the no-changes sentinel:

```bash
if grep -qE "^No (uncommitted changes to review|changes to review relative to)" "$PROMPT_FILE"; then
  # stop and inform the user there are no changes to review
fi
```

Use the persona prompt as a system or instruction prompt when the backend supports that separation. Use the canonical review prompt as the user prompt.

For Copilot backends, build a combined input file because `gh copilot` does not provide a separate system prompt channel:

```bash
{
  cat "$PERSONA_DIR/<persona>.txt"
  echo
  echo "Apply the lens above exclusively. Disregard any generic role framing in the instructions below."
  echo
  echo "---"
  echo
  cat "$PROMPT_FILE"
} > "$OUTPUT_DIR/input-<persona>"
```

Claude and Codex personas consume the persona prompt separately from `$PROMPT_FILE`. Copilot and Agy personas consume their combined input file.

When a persona is routed to Agy, create its combined input file using the same format above before launching Agy. Agy receives the persona instructions and canonical review prompt as one positional prompt.

Do not re-run `review-code`.

## Execution

Output filenames are opaque identifiers. Each persona writes to a unique file inside `$OUTPUT_DIR`. The manifest is the source of truth for the mapping from persona name to output file.

Run selected personas independently. Prefer parallel execution when supported. Set a timeout of 600000 milliseconds for each reviewer process.

Only write a manifest entry on success, determined by exit code, not by whether stdout is empty. A failed persona's output, including partial stdout, is not a valid review result and must not receive a manifest entry.

### Preflight Auth Check

Before launching any persona, check auth once for each backend actually selected — do not discover an auth failure after personas have already been timing out for up to 600s each:

- If any selected backend is `claude`: run `env -u ANTHROPIC_API_KEY claude auth status --json` once. If it does not report a logged-in session, stop and tell the user to run `claude login` (unless the user explicitly requested API-key auth, in which case a non-empty `ANTHROPIC_API_KEY` is sufficient — see [Backends](#backends)).
- If any selected backend is `copilot-gemini` (or another `gh copilot`-backed override): run `gh auth status` once. If it exits nonzero, stop and tell the user to run `gh auth login`.

Report both checks' failures immediately, before launching any backend. Do not retry the checks.

### Reviewer Working Directory

Backend CLIs (notably `codex`) auto-load configuration from the current working directory on startup — `.codex/config.toml`, `.agents/`, etc. Running them from the audited repo's root means they load that repo's own agent configuration, which can break sandboxed startup (for example, `--sandbox read-only` rejecting the PATH-alias step Codex performs during init).

The canonical prompt already contains the full diff, so reviewers do not need to start inside the repository. Launch every backend from a neutral working directory using absolute paths only:

```bash
REVIEWER_CWD=$(mktemp -d)
```

Wrap each invocation in a subshell that `cd`s into `$REVIEWER_CWD` first, as shown in [Default Backend Invocations](#default-backend-invocations). Every path passed to a reviewer (`$PROMPT_FILE`, `$PERSONA_DIR/<persona>.txt`, `$OUTPUT_DIR/...`) must already be absolute for this to work.

If a backend still fails to initialize from a neutral directory, treat it as a genuine startup failure — do not retry inside the repo root, since that reintroduces the same repo-local config problem. Report the exact startup error (see [Error Handling](#error-handling)).

### Copilot Prompt Limit and Fallback

`gh copilot` passes the combined prompt via `--prompt` as a command-line argument. Before invoking any `copilot-*` backend, check the combined input file size. If it exceeds the safe limit derived from the operating system argument limit, do not skip the persona. Reroute that persona to a non-Copilot backend using the same persona prompt and canonical review prompt.

Use this shell approximation when no better runtime helper is available:

```bash
if [ "$(wc -c < "$OUTPUT_DIR/input-<persona>")" -gt 200000 ]; then
  BACKEND_<persona>=codex
fi
```

Fallback order for an oversized Copilot prompt:

1. `codex`, or `claude` if the audit is already running inside Codex (where `codex` is unavailable)
2. the other of `codex`/`claude`
3. `agy`, only if both `codex` and `claude` have failed

When a persona is rerouted, record the actual backend used in the summary. Do not include the failed Copilot attempt in `Failed personas`; it was never invoked.

### Default Backend Invocations

**Auditor - codex**

```bash
(
  cd "$REVIEWER_CWD" && \
  codex exec --sandbox read-only --ephemeral --skip-git-repo-check \
    -o "$OUTPUT_DIR/audit-output-NN" \
    "$(cat "$PERSONA_DIR/auditor.txt")" < "$PROMPT_FILE" \
    2> "$OUTPUT_DIR/auditor.stderr"
)
if [ $? -eq 0 ]; then
  printf '%s\t%s\n' "auditor" "$OUTPUT_DIR/audit-output-NN" \
    >> "$OUTPUT_DIR/manifest.tsv"
fi
```

`$REVIEWER_CWD` is a bare `mktemp -d` directory, not a git repository — without `--skip-git-repo-check`, `codex exec` refuses to start there ("Not inside a trusted directory"), failing every single invocation. This is not an edge case; omit the flag and Auditor never runs.

When Codex is unavailable because the audit is running inside Codex, reroute Auditor to `claude` first; only use `agy` if `claude` is also unavailable or fails.

**Auditor rerouted to claude:**

```bash
(
  cd "$REVIEWER_CWD" && \
  env -u ANTHROPIC_API_KEY claude --system-prompt "$(cat "$PERSONA_DIR/auditor.txt")" \
    --print < "$PROMPT_FILE" \
    > "$OUTPUT_DIR/audit-output-NN" \
    2> "$OUTPUT_DIR/auditor-claude.stderr"
)
if [ $? -eq 0 ]; then
  printf '%s\t%s\n' "auditor" "$OUTPUT_DIR/audit-output-NN" \
    >> "$OUTPUT_DIR/manifest.tsv"
fi
```

**Auditor rerouted to agy (last resort):**

```bash
(
  cd "$REVIEWER_CWD" && \
  agy --sandbox --print \
    "$(cat "$OUTPUT_DIR/input-auditor")" \
    --print-timeout 600s \
    --log-file "$OUTPUT_DIR/auditor-agy.log" \
    > "$OUTPUT_DIR/audit-output-NN" \
    2> "$OUTPUT_DIR/auditor-agy.stderr"
)
if [ $? -eq 0 ]; then
  printf '%s\t%s\n' "auditor" "$OUTPUT_DIR/audit-output-NN" \
    >> "$OUTPUT_DIR/manifest.tsv"
fi
```

**Adversary - claude**

```bash
(
  cd "$REVIEWER_CWD" && \
  env -u ANTHROPIC_API_KEY claude --system-prompt "$(cat "$PERSONA_DIR/adversary.txt")" \
    --print < "$PROMPT_FILE" \
    > "$OUTPUT_DIR/audit-output-NN" \
    2> "$OUTPUT_DIR/adversary.stderr"
)
if [ $? -eq 0 ]; then
  printf '%s\t%s\n' "adversary" "$OUTPUT_DIR/audit-output-NN" \
    >> "$OUTPUT_DIR/manifest.tsv"
fi
```

**Pragmatist - copilot-gemini**

```bash
(
  cd "$REVIEWER_CWD" && \
  gh copilot -- --model gemini-3.5-flash --prompt "$(cat "$OUTPUT_DIR/input-pragmatist")" --silent \
    > "$OUTPUT_DIR/audit-output-NN" \
    2> "$OUTPUT_DIR/pragmatist.stderr"
)
if [ $? -eq 0 ]; then
  printf '%s\t%s\n' "pragmatist" "$OUTPUT_DIR/audit-output-NN" \
    >> "$OUTPUT_DIR/manifest.tsv"
fi
```

If the Copilot process exits nonzero and its stdout or `pragmatist.stderr` indicates exhausted quota or tokens, reroute that persona using the same persona prompt and canonical review prompt.

Same fallback order as an [oversized Copilot prompt](#copilot-prompt-limit-and-fallback): `codex`, then `claude`, then `agy` only as a last resort — see that section for the nested-Codex adjustment. Agy has been unreliable for this use case (see the Agy row in [Error Handling](#error-handling)) — even a clean exit code does not mean it produced a usable review. Treat its output with more scrutiny than the other backends, and do not be surprised if it produces nothing usable at all.

**Pragmatist rerouted to codex:**

```bash
(
  cd "$REVIEWER_CWD" && \
  codex exec --sandbox read-only --ephemeral --skip-git-repo-check \
    -o "$OUTPUT_DIR/audit-output-NN" \
    "$(cat "$PERSONA_DIR/pragmatic.txt")" < "$PROMPT_FILE" \
    2> "$OUTPUT_DIR/pragmatist-codex.stderr"
)
if [ $? -eq 0 ]; then
  printf '%s\t%s\n' "pragmatist" "$OUTPUT_DIR/audit-output-NN" \
    >> "$OUTPUT_DIR/manifest.tsv"
fi
```

**Pragmatist rerouted to claude:**

```bash
(
  cd "$REVIEWER_CWD" && \
  env -u ANTHROPIC_API_KEY claude --system-prompt "$(cat "$PERSONA_DIR/pragmatic.txt")" \
    --print < "$PROMPT_FILE" \
    > "$OUTPUT_DIR/audit-output-NN" \
    2> "$OUTPUT_DIR/pragmatist-claude.stderr"
)
if [ $? -eq 0 ]; then
  printf '%s\t%s\n' "pragmatist" "$OUTPUT_DIR/audit-output-NN" \
    >> "$OUTPUT_DIR/manifest.tsv"
fi
```

**Pragmatist rerouted to agy (last resort):**

```bash
(
  cd "$REVIEWER_CWD" && \
  agy --sandbox --print \
    "$(cat "$OUTPUT_DIR/input-pragmatist")" \
    --print-timeout 600s \
    --log-file "$OUTPUT_DIR/pragmatist-agy.log" \
    > "$OUTPUT_DIR/audit-output-NN" \
    2> "$OUTPUT_DIR/pragmatist-agy.stderr"
)
if [ $? -eq 0 ]; then
  printf '%s\t%s\n' "pragmatist" "$OUTPUT_DIR/audit-output-NN" \
    >> "$OUTPUT_DIR/manifest.tsv"
fi
```

An exit code of 0 with empty stdout is a valid result: the persona found nothing. A nonzero exit code is always a failure, regardless of what was written to stdout — never report it as "no findings." Read the matching `.stderr` file to report or diagnose the failure.

If a persona is assigned or rerouted to a backend other than its default, use that backend's persona-delivery mechanism from the [Backends](#backends) table, following the invocation shape of the matching example above (`claude` → system prompt with `ANTHROPIC_API_KEY` removed unless API-key auth was explicitly requested, `codex` → instruction prompt, `copilot-gemini` → combined prompt, `agy` → combined positional prompt).

## Execution Quorum

Execution quorum is separate from confidence tiers.

Default execution quorum: 2 successful personas.

If fewer than 2 personas succeed, abort the audit and report which personas failed.

## Finding Extraction

Read `$OUTPUT_DIR/manifest.tsv` to map each output file to its persona name. Parse and normalize each output file into a common finding structure:

```text
ReviewFinding

persona
severity
file
line
title
description
fix
category
confidence
fingerprint
verdict
```

Required fields:

- persona
- severity
- title
- description

Preferred fields:

- file
- line
- fix
- category
- confidence
- fingerprint
- verdict

If a persona returns prose instead of structured findings, extract findings conservatively.

Do not invent file or line references.

## Semantic Clustering

Merge findings that describe the same underlying issue.

Equivalent findings may use different wording. They should cluster into one issue if they refer to the same code path and same failure mode.

Clustering should consider:

- file
- line or nearby line range
- affected symbol or function
- bug category
- described failure mode

## Confidence Tiers

Replace flat agreement-count ordering with confidence tiers derived from cross-persona agreement:

| Tier            | Definition                                      |
|-----------------|-------------------------------------------------|
| cross-validated | Reported by all active personas                 |
| consensus       | Reported by 2 or more personas, but not all     |
| disputed        | Reported by 1 persona and challenged by another |
| solo            | Reported by exactly 1 persona, unchallenged     |

Present tiers in order: cross-validated, consensus, disputed, solo.

Within each tier, order by severity, then file path, then line number.

Cross-validated and consensus findings always appear.

Disputed and solo findings are hidden unless verbose mode is enabled, except when they explain a non-ship verdict. If the final verdict is `NEEDS-WORK` or `SHIP-WITH-CAVEATS`, include the highest-severity finding from each non-approving persona even when that finding is disputed or solo. The user must be able to see why a persona rejected or conditionally approved the change.

## Severity Reconciliation

When personas disagree on severity, choose the highest severity only if the description supports it.

Otherwise choose the median practical severity.

Severity order:

```text
CRITICAL
HIGH
MEDIUM
LOW
INFO
```

Do not inflate severity just because one persona used stronger wording.

## Verdict Reconciliation

Derive the final verdict from each successful persona's emitted `verdict:` line. If a persona did not emit a verdict, exclude it from the verdict count.

Use this summary scale:

- `SHIP`: all successful personas approved and there are no cross-validated or
  consensus findings, or the only remaining findings are `INFO`.
- `SHIP-WITH-CAVEATS`: at least one successful persona emitted `conditional`,
  or only `LOW` or `MEDIUM` findings remain.
- `NEEDS-WORK`: at least one successful persona emitted `rejected`, or at least
  one `CRITICAL` or `HIGH` finding remains.

If no verdicts were emitted, omit the verdict line and report that persona verdicts were unavailable.

## Verdict Drivers

Every non-ship verdict must be explainable from the visible output.

For each successful persona that emits `conditional` or `rejected`, identify the highest-severity finding from that persona and show it in a `Verdict drivers` section if it is not already visible in a cross-validated or consensus tier.

Each verdict driver must include:

- persona
- backend actually used
- verdict
- severity
- file and line, when available
- title
- fix, when available

If a persona emits `conditional` or `rejected` but provides no finding, say that explicitly:

```text
Pragmatist: rejected, but emitted no extractable finding. Treat this reviewer output as incomplete and inspect the raw persona output before acting on the verdict.
```

Do not let a hidden solo finding be the only reason for `NEEDS-WORK`.

## Output

Present findings grouped by confidence tier, then by severity:

```text
NEEDS-WORK (1/3 approved, 1/3 conditional, 1/3 rejected)

Successful personas: auditor, adversary, pragmatist
Failed personas: none

Cross-validated findings: 1
Consensus findings: 2
Disputed findings: 0
Solo findings: 0

## Verdict Drivers

Auditor (`codex`): rejected
CRITICAL
auth.py:42

Missing authorization check before account update.

Fix: Add `require_permission()` before any state mutation in this handler.

Pragmatist (`copilot-gemini`): conditional
HIGH
worker.py:88

Retry loop can repeat a non-idempotent operation.

Fix: Add an idempotency key or move the retry boundary around only the safe read operation.

## Cross-Validated

CRITICAL
auth.py:42

Missing authorization check before account update.

Fix: Add `require_permission()` before any state mutation in this handler.

Personas:
- auditor
- adversary
- pragmatist

## Consensus

HIGH
worker.py:88

Retry loop can repeat a non-idempotent operation.

Fix: Add an idempotency key or move the retry boundary around only the safe read operation.

Personas:
- auditor
- pragmatist
```

If there are no cross-validated or consensus findings, say no finding met the confidence threshold. Do not claim the code is correct.

If the verdict is `NEEDS-WORK` or `SHIP-WITH-CAVEATS`, include `Verdict drivers` before the confidence-tier findings. This section is required even at default verbosity.

If verbose mode is enabled, include disputed and solo findings after consensus findings, clearly separated by tier.

## Failure Handling

A persona has failed when its process exits nonzero, regardless of what was written to stdout. Empty stdout with a zero exit code is a successful run with no findings — do not conflate the two. Determine success from the exit code captured in [Default Backend Invocations](#default-backend-invocations), never from output emptiness alone.

If a persona fails:

- Continue if at least 2 personas succeed.
- Exclude failed personas from confidence tiers and verdict counts.
- Report failures in the summary, including the exit code and the relevant `.stderr` file content.

If fewer than 2 personas succeed:

- Abort the adversarial audit.
- Report failed personas and suggested fixes.

If aggregation fails:

- Stop immediately.
- Report the failure.
- Clean up scratch files.

## Error Handling

| Error                           | Action                                                                  |
|---------------------------------|-------------------------------------------------------------------------|
| `review-code` unavailable       | Stop and report that the review prompt generator is unavailable.        |
| `review-code` failed            | Stop immediately. Check `$REVIEW_CODE_EXIT`, not `$PROMPT_FILE` content — a nonzero exit means the file is not a usable prompt (for example, an invalid `$BASE_REF`). |
| No changes to review            | Stop and report that there are no changes to audit (uncommitted, or relative to `$BASE_REF`). |
| `gh: command not found`         | Tell user: `brew install gh && gh extension install github/gh-copilot`  |
| `gh copilot` missing            | Tell user: `gh extension install github/gh-copilot`                     |
| `agy: command not found`        | Tell user to install or enable the Agy CLI.                            |
| `claude: command not found`     | Tell user to install Claude Code.                                       |
| `codex: command not found`      | Tell user: `npm i -g @openai/codex`                                     |
| Codex nested inside an existing Codex sandbox (`CODEX_SANDBOX` or `CODEX_THREAD_ID` set) | Skip nested Codex and reroute to `claude`. Only fall back to `agy` if `claude` is also unavailable or fails. |
| Codex fails to initialize under `--sandbox read-only` (PATH-alias or app-server errors) | Confirm the invocation ran from `$REVIEWER_CWD`, not the repo root. If the audit is nested inside Codex, use the nested-Codex fallback above. Otherwise treat as a genuine failure and report the exact startup error from `auditor.stderr`. |
| Claude login authentication | Caught by the [Preflight Auth Check](#preflight-auth-check) before any persona launches. Run Claude with `ANTHROPIC_API_KEY` removed. If `claude auth status --json` does not report a logged-in session, tell the user to run `claude login`. |
| `gh` not authenticated (`gh auth status` exits nonzero) | Caught by the [Preflight Auth Check](#preflight-auth-check) before any persona launches. Tell the user to run `gh auth login`. |
| Copilot prompt exceeds limit   | Reroute that persona to `codex`, or `claude` if nested inside Codex; try the other of the two next; `agy` only if both have failed. Treat as failed only if every non-Copilot fallback fails. |
| Copilot quota exhausted        | Reroute that persona to `codex`, then `claude`, then `agy` as a last resort. Treat it as failed only if every fallback is unavailable or fails. |
| Agy produces no usable review even with a correct invocation | Known issue, tracked in an open GitHub issue (search for "agy" review-backend reliability). `agy --print` is a full agentic CLI, not a stateless text-completion tool: given a prompt that references a real file path, it has been observed to go search the filesystem for that file — in an unrelated local project it already knew about, not this repo and not `$REVIEWER_CWD` — instead of reviewing the supplied diff text, and finish with no findings and no verdict line despite exit 0. This is why `agy` is now a last-resort fallback rather than Pragmatist's first fallback. |
| Agy output is unrelated to the supplied prompt (exit 0) | The positional prompt must come immediately after `--print`, before any other flag. If a value-taking flag such as `--print-timeout` precedes the prompt, `agy` fails to bind the prompt argument and instead returns an unrelated self-referential response (e.g. explaining its own `--print-timeout` flag) — silently, with exit 0. The invocations above already use the correct order; if a future edit reorders them, this is the symptom to watch for. This is a separate failure mode from the filesystem-exploration issue above — fixing the argument order does not fix that one. |
| Agy's prompt is visible to other local processes | Confirmed: `agy` requires the prompt as a positional value after `--print` (stdin alone does not work — it falls into the flag-explanation failure above instead), so the full canonical prompt, diff content included, sits in `agy`'s argv for the life of the process and is readable via `ps`/`/proc/<pid>/cmdline` by anyone else who can list processes on that host. There is no verified mitigation for this; treat `agy` as unsuitable for auditing diffs that may contain secrets on a shared or multi-tenant host. |
| Agy misparses a prompt that starts with a flag-shaped token | Confirmed by direct test: prefixing the positional prompt with `--` does not make `agy` treat the following text as a literal value — it still attempted to parse tokens inside the prompt as flags and failed with `flags provided but not defined`. If a diff-derived prompt happens to contain text resembling a known `agy` flag in that position, the invocation can fail outright. No verified guard exists; this is inherent to `agy` being the last-resort fallback rather than a primary reviewer. |
| Reviewer timeout                | Exclude reviewer; continue if quorum remains.                           |
| Reviewer exits nonzero          | Treat as failed regardless of stdout content; report exit code and stderr.|
| Fewer than 2 successful reviews | Abort adversarial audit.                                                |
| Aggregation failed              | Stop and report failure.                                                |

## Cleanup

Scratch files must be removed regardless of success, failure, timeout, reviewer failure, or user cancellation.

The `trap` set in [Review Input](#review-input) is the primary cleanup mechanism — it removes `$PROMPT_FILE`, `$OUTPUT_DIR`, and `$REVIEWER_CWD` automatically when that shell session exits, including on error or interruption. Treat the steps below as an explicit fallback: run them if the trap-owning shell session has already ended (for example, execution moved to a new session) or if you cannot rely on shell traps in the current environment.

After aggregation completes, or on failure, run cleanup as an explicit final
step:

```bash
rm -f "${PROMPT_FILE:-}"
rm -rf "${OUTPUT_DIR:-}"
rm -rf "${REVIEWER_CWD:-}"
```

If the run was interrupted before cleanup, stale files remain under `.scratch/` and in the system temp directory. Inform the user and suggest:

```bash
rm -f .scratch/review-prompt.* && rm -rf .scratch/audit-outputs.*
```

`$REVIEWER_CWD` lives outside `.scratch/` (it must be a neutral directory, not one inside the audited repo) — if orphaned, it is an empty temp directory with no reviewer output in it, since reviewers only ever write to `$OUTPUT_DIR`.

If debugging is needed, ask before preserving scratch files.

## Summary

At the end report:

- Verdict line synthesized from persona verdicts
- Audit mode
- Personas selected
- Backends used — for any rerouted persona, show both the requested default backend and the effective backend actually used (for example, `auditor: codex → claude, nested Codex`), so a reduction in model diversity is visible rather than silently reported as if it were the default
- Successful personas
- Failed personas
- Execution quorum
- Verdict drivers for any non-ship verdict
- Cross-validated findings count
- Consensus findings count
- Disputed and solo findings count, if verbose mode is enabled

# Examples

**Standard audit**

```text
User: /audit standard
Agent:
- Checks for untracked files.
- Runs review-code once.
- Consumes the prompt directly.
- Presents current-model findings.
```

**Adversarial audit**

```text
User: /audit adversarial
Agent: asks persona set if not specified.
User: all three
Agent:
- Checks for untracked files.
- Shows diff stats.
- Runs review-code once to create the canonical prompt.
- Passes Auditor and Adversary persona prompts through backend-supported system
  or instruction prompt channels.
- Builds a combined Pragmatist prompt because Copilot has no separate system
  prompt channel.
- Runs the personas on their assigned default backends.
- Extracts findings and verdict lines from each persona output.
- Clusters equivalent findings across personas.
- Shows verdict drivers for non-ship persona verdicts.
- Reports cross-validated and consensus findings by default.
- Reports disputed and solo findings in verbose mode, plus any hidden findings
  required to explain the final verdict.
- Verdict: SHIP-WITH-CAVEATS (2/3 approved, 1/3 conditional)
```
