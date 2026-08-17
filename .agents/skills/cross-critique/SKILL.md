---
name: cross-critique
description: Run a second round on a contested question by circulating each subagent's independent proposal to the other authors and asking for structured pros and cons, then synthesize. Use this skill whenever you have multiple independent proposals or opinions on a contested decision — architecture tradeoffs, code review disagreements, design choices, competing root-cause theories — and want sharper analysis than you'd produce by synthesizing alone. Pairs naturally with the council and research skills; reach for it liberally whenever proposals diverge.
---

# Cross-Critique

Use this skill to run a **second round** after several subagents have independently produced proposals or opinions on the same contested question. Instead of synthesizing their reports yourself, circulate each proposal to the *other* authors, ask them to critique it, then synthesize the richer set of analyses that results.

## Cross-Critique Protocol

```text
Independent proposals
        ↓
Proposal exchange
        ↓
Cross critique
        ↓
Author self-revision
        ↓
Evidence-weighted synthesis
```
This protocol deliberately separates **proposal generation** from **proposal evaluation**. Independent generation maximizes diversity; cross-critique exposes hidden assumptions and tradeoffs; synthesis integrates the resulting evidence into a final recommendation.

## Core Invariants

### Independent First Round

Round one must be independent.

Authors must not see each other's proposals before producing their own, or diversity collapses before critique begins.

### Shared Inputs

Every author critiques the same set of competing proposals.

Do not give different reviewers different subsets of proposals.

### Critique Proposals, Not Authors

Critique the reasoning, assumptions, tradeoffs, and evidence—not the author.

### Self-Reflection

After reviewing competing proposals, each author may revise their own recommendation before synthesis.

Changing one's mind is expected when presented with stronger evidence.

### Evidence Over Votes

During synthesis, compare critiques by **evidence quality**, not vote count.

Agreement is informative; evidence is decisive.

### Human (or Orchestrator) Synthesis

The final recommendation comes from synthesizing the critiques, not from majority voting or averaging rankings.

## Why this matters

Independent authors usually develop a deeper understanding of the problem than the orchestrator because they performed the investigation themselves. If you immediately synthesize their reports, you only benefit from the tradeoffs you happen to notice.

Cross-critique lets authors challenge the assumptions and tradeoffs in competing proposals. They often identify hidden failure modes, overlooked edge cases, and stronger arguments that neither the orchestrator nor the original proposal captured.

This mirrors effective technical decision making: gather independent opinions first, then let informed participants challenge one another before reaching a conclusion.

## Prerequisite: Independent proposals first

This skill is the **second round**.

It assumes you already have multiple independent proposals.

If you do not:
- For judgment-heavy decisions, generate proposals with the **council** skill.
- For investigation-heavy questions, generate proposals using parallel subagents (see the **research** skill).
- Or use any existing set of independent proposals.

The first round must remain independent. Authors should not see one another's work before completing their own proposal.

## When to use it

Use cross-critique when a decision is genuinely **contested**:

- Architecture and design tradeoffs.
- Code review where reviewers disagree.
- Competing root-cause theories.
- API or code-structure decisions with multiple reasonable approaches.
- Any subjective engineering decision with credible competing proposals.

Do not use cross-critique when:

- Independent proposals already converge.
- The question has an objective answer that can be verified directly.
- Additional critique is unlikely to uncover meaningful new evidence.

Cross-critique adds latency and tokens. Its value comes from improving contested decisions, not routine ones.

## How to do it

### 1. Assemble the proposals

Collect each author's proposal.

Keep only the essential recommendation and supporting reasoning rather than full transcripts.

Prefer anonymizing proposals when practical:

```text
Proposal A
Proposal B
Proposal C
```

This reduces anchoring and reputation effects.

### 2. Circulate for structured critique

Reuse the **same subagents** from round one.

They already possess the investigation context and can critique from an informed position.

Send each author every proposal **except their own**.

For each competing proposal ask for:

- **Pros** — what it gets right; where it is stronger than my proposal.
- **Cons** — risks, hidden assumptions, edge cases, weaknesses.
- Whether I would **revise my own recommendation**, and why.
- My **updated recommendation** with confidence after considering all alternatives.

Require both strengths and weaknesses.

An honest critique that acknowledges competing proposals is more valuable than a reflexive defense of one's own work.

### 3. Synthesize

Review the critiques.

Evaluate arguments by **evidence quality**, not vote count.

In the final synthesis:

- Lead with the recommendation.
- Describe where authors converged after seeing competing proposals.
- Highlight the strongest objections raised against each option.
- Explain why the recommended option survives critique better than the alternatives.
- Call out remaining disagreement, uncertainty, and important unknowns.

## Final answer template

```markdown
## Recommendation
[One or two sentences.]

## How the critiques changed the picture
- [Where authors converged]
- [Most important objection]
- [Whether anyone revised their position]

## Why this option wins
- [Reason supported by the critiques]

## Remaining risks and unknowns
- [Open questions or caveats]
```

## Practical notes

- Keep the critique round read-only unless the underlying task explicitly involves making changes.
- Do not expose internal subagent identities unless the user asks.
- If a critique is weak or unsupported, send a focused follow-up to that same author rather than discarding it.
- Prefer one high-quality critique round over multiple shallow critique rounds.
