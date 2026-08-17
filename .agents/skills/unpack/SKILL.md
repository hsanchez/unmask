---
name: unpack
description: Reach explicit shared understanding with the user on an idea, feature, or request through structured, iterative questioning before any plan is drafted. Escalate here when `clarify` already asked its minimal questions but the user still disagrees with the interpretation or won't confirm scope. Also use when the user explicitly asks to "unpack" an idea, walk through it, or make sure you're both on the same page. Produces a short in-conversation Design Brief, not a PRD or file artifact.
---

# Unpack

Reach agreement with the user before planning starts. This skill is the escalation path from `clarify`: `clarify` asks a handful of must-have questions once and moves on; `unpack` keeps interviewing, one question at a time, breaking the idea down decision by decision, until the user explicitly confirms the shared understanding — or until it's clear alignment isn't happening and the user needs to decide how to proceed.

Unpacking produces a **Design Brief** — a compact, conversational summary of decisions and assumptions — not a persisted document. Only write it to disk if the user asks to save it or if the task is long-running enough to need a handoff (then follow the `handoff` skill's conventions).

## When to Use

- **Escalation from `clarify`**: `clarify` ran, questions were answered, but the user disagrees with the resulting interpretation, keeps redirecting, or won't confirm scope. Don't re-run `clarify` — switch to `unpack`, and carry forward everything `clarify` already established (see step 0 below).
- **Explicit invocation**: the user says things like "let's unpack this", "walk me through it with me", "make sure we're on the same page before you start".
- **Multi-axis ambiguity**: the request touches several unresolved decisions at once (features, priorities, user flow, tech choices) rather than one missing fact.

## When NOT to Use

- A single missing detail blocks progress — use `clarify` instead.
- The user wants a durable spec for handoff to other engineers/stakeholders — that needs a heavier PRD-style flow, not this skill.
- The user already agrees on scope and just wants a plan — skip straight to planning.

## Core Rule

Do not draft a plan until the user has explicitly confirmed a reflected-back summary. Silence, moving on, or a vague "ok" is not confirmation — get an explicit yes.

## Progress Tracking

Copy this checklist into the conversation at the start and update it as you go. Keep it in-context only — don't write it to a file unless the user asks to persist the brief (see Reflected Design Brief).

```
Unpack Progress:
- [ ] Carry forward prior answers (from `clarify`, if escalated)
- [ ] Walk relevant question-tree branches
- [ ] Resolve dependent decisions in order (don't ask a dependent question before its prerequisite is settled)
- [ ] Answer anything discoverable via codebase/research instead of asking
- [ ] Flag any conflicts (stakeholder disagreement, research contradicting stated assumptions)
- [ ] Present reflected Design Brief
- [ ] Get explicit user confirmation (or name the unresolved fork if stuck, and wait)
- [ ] Iterate on brief if user pushes back, noting what changed each round
- [ ] Hand off to planning (or note assumptions if user says proceed with best judgment)
```

## Conversation Flow

0. **If escalating from `clarify`**: restate the answers already collected in one line ("From before: scope = minimal change, compatibility = current defaults") and don't ask those again. Only the new interview should target what's still unresolved or contested.
1. Briefly state you'll ask a few questions to get aligned before drafting a plan.
2. Ask **one question at a time**. Use the interactive question tool if available; otherwise ask conversationally.
3. Split effort ~70% understanding the idea / 30% educating on options and tradeoffs.
4. For every question, offer a recommended default (bolded) — never leave the user without a suggested answer.
5. Track assumptions as you go; surface them in the reflected summary, not buried in the conversation.
6. Before finalizing, explore the codebase (or use `research`) to answer anything discoverable — don't ask the user something you can find yourself. If research contradicts something the user assumed (e.g., they think a component doesn't exist yet, but it does), don't silently override or silently defer — surface it explicitly as a flagged item before folding it into the brief.
7. If the request relays other stakeholders' input ("my team wants X, but I think Y"), don't collapse it into a single uncontested line — ask which position should win, or carry both forward as an explicit open question in the brief.
8. Periodically reflect back: "So if I understand correctly, you're building/asking for [summary]. Is that accurate?" Only proceed to planning after an explicit yes.

## Question Tree

Not every branch applies every time — walk only the branches relevant to the request, but resolve dependencies between decisions before moving to dependent ones.

1. **High-level goal** — "Tell me about the idea/change at a high level."
2. **Core capabilities** — "What are the 3–5 things this needs to do to be valuable?"
3. **Priorities** — "Which of these are must-have for a first pass vs. nice-to-have later?"
4. **Primary flow** — "Walk me through what happens from start to the user's/caller's main goal." (skip if no user-facing flow, e.g., a script or backend job)
5. **Constraints** — tech stack preferences, performance, compatibility, existing patterns in the repo.
6. **Non-goals** — "What should this explicitly NOT do or NOT try to solve right now?"
7. **Success criteria** — "How will we know this is done/working?"
8. **Assumptions check** — restate assumptions you're making about users, environment, or scope; ask the user to confirm or correct.

Only ask about accessibility or performance if the user raised it, or the artifact clearly has a UI/perf-sensitive surface — don't pad the interview with irrelevant checklist items.

## Questioning Patterns

- Start broad: "Tell me about this at a high level."
- Priorities: "Which of these are must-haves for the first version?"
- Journeys: "Walk me through what happens end to end."
- Alternatives: "What similar things exist? What should this do differently?"
- Assumptions: "What are you assuming about users, environment, or scale?"
- Reflective checkpoint: "So if I understand correctly, [summary]. Accurate?"

## Technology Discussions

- Offer 3–4 options with brief pros/cons, stay conceptual.
- Give a clear recommendation with one-line reasoning.
- If the user already stated a preference, use it to steer recommendations instead of re-litigating it.
- Don't introduce accessibility or performance tasks unless the user asked or the artifact obviously needs them (has a UI, is latency-sensitive, etc.).

## Reflected Design Brief (in-conversation, not a file)

When ready to check alignment, present a compact brief:

```text
Design Brief
Goal:
Core capabilities (must-have):
Deferred / nice-to-have:
Primary flow:
Key constraints:
Non-goals:
Success criteria:
Assumptions:
Flagged conflicts (stakeholder disagreement / research vs. stated assumptions):
Open questions (if any):
```

Ask: "Does this match what you have in mind? Anything to change before I plan the implementation?"

Only exit the loop on an explicit confirmation. If the user pushes back, treat it as a new answer and update the brief — don't restart the whole interview. When re-presenting the brief after pushback, lead with a one-line "Changed since last round:" summary before the full brief, so the user isn't re-reading unchanged sections to find what's different.

## Exit Conditions

- **Aligned**: user explicitly confirms the brief. Hand off to planning (do not draft the plan inside this skill — that's the caller's job).
- **Still stuck (won't converge, wants to keep trying)**: cap at **3 reflected briefs**. If three consecutive reflected briefs fail to get explicit confirmation, stop interviewing and name the specific unresolved fork(s) plainly. This is a hard stop, not a soft one: do not auto-fall-through to "proceed with best judgment" or any other exit. Wait for the user's explicit next move — they may confirm one side of the fork, ask to keep going anyway, invoke best-judgment themselves, or abandon. Treat whichever the user chooses as a new, separate exit condition below; naming the fork is not itself an exit.
- **Abandon (wants to stop entirely)**: if the user wants to table the idea rather than proceed under any interpretation, confirm that explicitly and end the skill without handing off to planning — don't default this into "best judgment."
- **User cancels interviewing but wants to proceed**: if the user says to just proceed with best judgment, still produce a short assumptions list (goal, scope, and any flagged conflicts, at minimum) before handing off — don't skip straight to planning with unstated assumptions. Note clearly that these are unconfirmed. This exit must be user-initiated — reaching the round cap never triggers it automatically.

## Relationship to Other Skills

| Situation | Use |
| --- | --- |
| One missing fact, quick fix | `clarify` |
| User disagrees after `clarify`, or explicit "let's unpack this" | `unpack` (this skill) |
| Needs codebase facts to answer a question | `research` |
| Judgment-heavy technical decision after alignment | `council` or `cross-critique` |
| Long-running task needing context handoff | `handoff` |
| Executing an already-agreed plan | `saga` when explicitly enabled |
