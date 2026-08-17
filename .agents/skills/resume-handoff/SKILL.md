---
name: resume-handoff
description: Load a handoff document to resume work from a previous session.
argument-hint: "Path to the handoff file, e.g. ~/.handoffs/<repo-name>/handoff-2026-06-20_14-30-00.md"
---

If no argument is passed, run `ls ~/.handoffs/<repo-name>/` (where `<repo-name>` is `basename $(git rev-parse --show-toplevel)`) and ask the user to pick a file.

Before reading the handoff, sync project state:
1. Re-read the project's `.rules` file.
2. Read `MEMORY.md`.
3. If `MEMORY.md` is sparse, read `MEMORY_LOG.md`.
4. Run `git status` and `git log --oneline -5` — compare against what the handoff describes; alert the user if significant drift is detected.

Then read the handoff document at the given path and internalize its contents to resume work from a previous session.

Present a structured summary to the user covering: tasks in progress, outstanding issues, failed approaches to avoid, and the immediate next step.

**IMPORTANT:** Wait for explicit user confirmation before taking any action. Do not auto-start.

Upon confirmation:
- Recreate the todo list from the handoff using `TodoWrite`; if `TodoWrite` is unavailable, use the closest available session plan tool
- Mark the current task as in_progress
- Consult the **Failed Approaches** section before attempting any solution
- Begin work on the first item in **Action Items & Next Steps**
