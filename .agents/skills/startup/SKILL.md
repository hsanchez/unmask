---
name: startup
description: Readiness and synchronization check. Use before starting any task, or when the user asks "are you on?", to refresh repo rules, memory, and recent project context.
---

At repo root, ask: "Resume from a handoff file?" and wait for an answer.

If the user provides a handoff path, use the `resume-handoff` skill with that path — it performs its own baseline sync before loading the handoff.

If the user wants to resume but has not provided a path, ask for the handoff path.

If the user declines or has no handoff:

1. Read `.rules` (skip if you have already read it during the current session).
2. Read `MEMORY.md`. If `MEMORY.md` has no substantive content beyond headings or whitespace, read `MEMORY_LOG.md`.
3. Also read `MEMORY_LOG.md` when debugging, recovering context, or tracing prior decisions.
4. Compress every model response according to the Response Compression protocol in `.rules`.
