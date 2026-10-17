# PROJECT MEMORY

## Entries

### What it is (as of 2026-10-16)
- Unmask 0.1.0, unreleased (no tags). Finds over-strict rules, contradictions and ambiguities in natural-language and regex guardrails with Z3; diagnoses reported regex incidents; checks judge protocol adherence from annotated replays.
- Surfaces: library (`unmask.bugs`, `unmask.adherence`, `VerificationCase`), CLI (`scan`, `diagnose`, `adherence`, `serve`), local Flask studio in plain JS with no build step.
- Natural-language analysis needs a model provider (gemini, openai, anthropic extras); regex analysis and adherence run locally.

### Layout and layering
- Python modules: `models -> backend -> service -> repair -> webapp/cli`; adherence lives in `judge.py` (+ `solver.py`). `repair.py` holds fix generation and validation; `ObservedBackend` is in `backend.py`.
- Studio scripts are classic scripts sharing global scope, listed in load order in `index.html`: `audit-state`, `audit`, `studio-core/storage/studies/imports/controls/findings`, `app`. The server sends `Cache-Control: no-store`, so there is no cache-busting query.
- Docs: `README.md` (quickstart, commands), `docs/studio.md`, `docs/schemas.md`, ADRs in `docs/decisions/` (001 verified fixes, 002 replay judge audits, 003 canonical `VerificationResult`).

### Decisions worth remembering
- `VerificationResult` is the single adherence result; `to_dict()` is the studio wire format and must stay byte-identical (key order included). Wire names such as `auditorVersion` and routes `/api/v1/judge-audits` are intentionally unchanged.
- No `check` command: `scan` dispatches on specification kind.
- Proposed fixes are model-authored, then verified by re-running the analysis; a separate `proposed_fixes.py` was tried and removed.
- `basedpyright` runs in prek at zero errors and warnings over `src`, `tests`, `examples`.

### Working preferences
- Keep the compact studio UI; no broad visual redesigns without tight visual validation (an earlier redesign was rolled back).
- Avoid new small files; prefer existing modules. A new module needs a real layering reason.
- "simplify-code" means running `scripts/simplify-code` and applying its checklist, not the built-in `/simplify` skill.
- Adversarial audits go through the `audit` skill before commits that change logic; the user commits.
- Issues are waived for this work; `PLAN.md` is local and git-ignored.

### Verification baseline (2026-10-16)
- `uv run prek run --all-files` clean; 607 Python tests with `-W error`; 75 Node frontend checks (`node --test tests/frontend/*.test.cjs`); 98% coverage; installed-wheel smoke test passes.
- Not covered: live provider calls and browser UI in the repo (UI was checked with a throwaway headless-Chrome script, not committed).

### Known leftovers
- Long pre-existing functions (`propose_fix`, `audit`, `create_app`); about 50 uncovered defensive lines (regex worker child process, unreachable guards).
- Untracked `2601.17789v1.pdf` is intentionally excluded from commits.
