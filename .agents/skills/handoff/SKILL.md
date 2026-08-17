---
name: handoff
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: "What will the next session be used for?"
---

Write a handoff document summarising the current conversation so a fresh agent can continue the work. The handoff document is optimized for AI consumption — dense, structured, no fluff. Prefer `file:line` references over descriptions or code snippets.

Save to `~/.handoffs/<repo-name>/handoff-$(date +%Y-%m-%d_%H-%M-%S).md`, creating the directory if it does not exist. Derive `<repo-name>` from `basename $(git rev-parse --show-toplevel)`.

Include the following sections:

- **Context**: run `git rev-parse --abbrev-ref HEAD` for branch, `git rev-parse HEAD` for commit hash, and `basename $(git rev-parse --show-toplevel)` for repo name
- **Tasks**: each task with its status (completed / in progress / planned)
- **Learnings**: root causes, patterns, or non-obvious findings discovered during the session
- **Failed Approaches**: dead ends tried and why they failed — the next agent must not repeat these
- **Outstanding Issues**: blockers, open questions, or unknowns that need resolution before work can proceed
- **Artifacts**: exhaustive list of files touched or produced, as `path/to/file:line` references
- **Action Items & Next Steps**: concrete things the next agent should do first
- **Mandatory First Steps**: what the resuming agent must verify on arrival. Always include: `git status`, `git log --oneline -5`, read `.rules`, `MEMORY.md`; if `MEMORY.md` is sparse, also read `MEMORY_LOG.md`
- **Suggested Skills**: skills the next agent should invoke

Do not duplicate content already captured in other artifacts (PRDs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.

After saving, tell the user the exact file path so they can reference it in the next session.
