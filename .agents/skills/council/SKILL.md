---
name: council
description: Run a model-diverse subagent council to investigate the same problem from multiple perspectives, compare findings, and produce a final recommendation. Use this skill whenever the user asks for a council, second opinions, multiple agents/models to evaluate one question, parallel investigation, red-team/blue-team comparison, or help deciding between competing technical approaches.
---

# Council

Use this skill to coordinate multiple subagents investigating the same question, with different models first and different assigned perspectives second, then synthesize their reports into one recommendation.

This skill is best for judgment-heavy tasks: architecture tradeoffs, risky bug fixes, code review red-teaming, rollout decisions, incident analysis, and “is this alternative worth pursuing?” questions.

## Council Protocol

```text
Frame the question
        ↓
Assemble the council
        ↓
Launch child agents
        ↓
Independent investigation
        ↓
Structured reports
        ↓
Evidence-weighted synthesis
```

Each council member investigates independently using the same question and decision criteria. The orchestrator synthesizes the resulting reports into the final recommendation.

## Core Invariants

### Independent Investigation

Council members investigate independently.

Do not expose one member's findings to another during the first round.

### Model Diversity Before Perspective Diversity

Maximize model diversity before introducing perspective diversity.

Only fall back to perspective diversity when model diversity is unavailable.

### Shared Question

Every council member answers the same decision question.

Do not give different members different decision criteria.

### Structured Evidence

Recommendations should be supported by concrete evidence rather than intuition.

### Evidence Over Votes

Compare reports by evidence quality, not by vote count.

Agreement is informative; evidence is decisive.

### Orchestrator Synthesis

The orchestrator produces the final recommendation.

Do not choose an option solely because most council members preferred it.

## Workflow

### 1. Frame the council question

State the decision the council should answer in one sentence.

Identify:

- the competing options or hypotheses;
- the codebase, branch, PR, issue, design, or artifact to inspect;
- whether members should be read-only or may make code changes;
- the decision criteria, such as correctness, implementation cost, rollout risk, testability, or product behavior.

If the request is ambiguous, ask only the minimum clarification needed. Otherwise choose sensible defaults and proceed.

### 2. Assemble the council

#### 2.1 Choose models

Prioritize model diversity.

A council should not default to three agents on the same model with different angles unless no meaningful model diversity is available or the user explicitly requests a single model.

Preferred default three-member council:

- Opus 4.7 or strongest available Claude/Opus model: architecture, correctness, and edge cases.
- GPT-5.5 or strongest available GPT/Codex model: implementation feasibility and testing.
- Strong open-source model (Kimi, GLM, etc.): contrarian analysis, hidden assumptions, and alternative framing.

If an exact model is unavailable, substitute the closest available model from the same family and briefly note the substitution.

If no suitable open-source model exists, use another frontier model while preserving model diversity where possible.

#### 2.2 Assign perspectives

Assign both a model and a perspective.

Perspectives should complement—not duplicate—the strengths of the selected models.

Examples:

- architecture / correctness;
- implementation / testability;
- security or red-team;
- performance;
- product risk;
- contrarian ("argue against the obvious solution").

Avoid assigning every member the same perspective.

#### 2.3 Configure execution

Prefer `.agents/skills/council/scripts/run-agents` when it is available.

If `.agents/skills/council/scripts/run-agents` is unavailable, use the active harness's native subagent-launch mechanism while preserving the same execution protocol.

The launch mechanism should support:

- assigning each child its model or closest supported substitute;
- assigning each child its perspective;
- passing the same council question and decision criteria to every child;
- enforcing read-only or isolated-worktree execution;
- collecting structured reports;
- sending follow-up questions to existing children when additional evidence is needed.

When using `.agents/skills/council/scripts/run-agents`, prepare a shared council brief file and launch each member as a backend:perspective specification.

Example:

```bash
.agents/skills/council/scripts/run-agents \
  --brief .scratch/council-brief.md \
  claude:architecture \
  codex:implementation \
  agy:contrarian
```

The launcher applies a 600-second timeout to Claude and Agy by default and
continues after a member failure or timeout. Set
`COUNCIL_AGENT_TIMEOUT_SECONDS` to change the limit. It uses `timeout` or
`gtimeout`; set `COUNCIL_TIMEOUT_COMMAND` when the executable has another name
or location.

The launcher should produce a per-run output directory containing report files, a manifest, and any failure logs. Treat the manifest as the source of truth for mapping council members to reports.

When the active launcher applies model selection per run rather than per child, launch separate runs for different models.

When using non-default harnesses, choose valid model identifiers for that environment.

Do not invent unsupported model identifiers.

For read-only investigations, keep all children in the same checkout and explicitly forbid edits.

For implementation or prototyping councils, give each child its own git worktree and branch.

### 3. Brief before launching

For explicit orchestration requests, briefly tell the user which council members will be launched and what each will investigate.

Wait for approval before launching child agents.

The shared brief should include:

- repository path or artifact location;
- current branch or context;
- exact decision question;
- relevant background;
- required files or symbols;
- execution constraints;
- expected report format.

Keep launch prompts concise.

If launch validation limits prompt length, launch with a minimal prompt and send the detailed brief immediately afterward.

### 4. Ask for structured reports

Ask every council member to report:

1. evidence inspected (files, symbols, documents);
2. current behavior or implementation;
3. evaluated alternative;
4. correctness risks and edge cases;
5. implementation and testing cost;
6. recommendation (current / alternative / hybrid);
7. confidence and unknowns.

Encourage independence.

Do not share one member's findings with another unless intentionally performing a second-round critique.

### 5. Collect reports

Read each report rather than relying solely on task completion.

If using `.agents/skills/council/scripts/run-agents`, read the launcher manifest to locate report artifacts and identify failed members.

If evidence is incomplete or unsupported, send a focused follow-up to the same member rather than launching a replacement.

Reuse existing children whenever possible because they retain context.

### 6. Synthesize

Compare reports by evidence quality, not vote count.

In the final recommendation:

- lead with the recommendation;
- summarize consensus and disagreements;
- explain why the recommended option best satisfies the decision criteria;
- explicitly address the user's concern;
- include important file paths or symbols without overwhelming the reader;
- distinguish immediate actions from future hardening;
- report confidence and remaining unknowns.

Prefer a concise decision memo rather than a transcript summary.

## Final answer template

```markdown
## Recommendation

[One or two sentences.]

## Why

- [Key reason]
- [Key reason]
- [Key reason]

## Tradeoffs and risks

- [Risk]
- [Testing or rollout implication]

## Final call

[Concrete next step.]
```

## Practical notes

- For read-only councils, instruct members not to edit files, commit, create branches, or open PRs.
- For implementation councils, follow the repository's normal version-control practices and isolate local work in separate worktrees.
- For code review councils, resolve review comments only after the underlying issue has actually been addressed.
- Do not expose internal child-agent identifiers unless the user explicitly requests them.
