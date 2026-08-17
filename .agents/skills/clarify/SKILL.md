---
name: clarify
description: Elicit clarifying requirements from the user before implementing. Use when a request has multiple plausible interpretations or when key details — objective, scope, constraints, environment, or safety — are missing. Pass an optional hint about which dimension seems underspecified.
---

# Clarify Requirements Before Acting

Use this skill when a request is ambiguous enough that proceeding would risk wrong work. The goal is to ask the minimum set of must-have questions, then confirm interpretation before touching any code.

Use the `clarify` skill with an optional free-text focus hint about which dimension seems underspecified. If a focus is provided, start there rather than running the full diagnostic.

## When to Use

Use this skill if, after a brief look at the request, any of the following are unclear:
- **Objective** — what changes vs. what stays the same
- **Done** — acceptance criteria, examples, edge cases
- **Scope** — which files, components, or users are in or out
- **Constraints** — compatibility, performance, style, deps, time
- **Environment** — language/runtime versions, OS, build/test runner
- **Safety** — data migration, rollback plan, blast radius

If multiple plausible interpretations exist, treat the request as underspecified.

## When NOT to Use

Skip this skill when the request is already clear, or when a quick, low-risk read of the repo (configs, existing patterns) would answer the missing detail without requiring user input.

## Workflow

### 1. Diagnose

If a focus hint was provided, go directly to step 2 targeting that dimension. Otherwise, run a brief diagnostic: inspect relevant configs or patterns to rule out questions you can answer yourself. Treat whatever remains as must-have.

### 2. Ask must-have questions (1–5 max)

Prioritize questions that eliminate entire branches of work. Make them easy to answer:
- Short, numbered questions — no paragraphs
- Offer lettered multiple-choice options where possible
- **Bold** the recommended/default option
- Include a "Not sure — use default" escape
- Include a `defaults` fast-path at the bottom

Example format:

```text
1) Scope?
   a) **Minimal change (default)**
   b) Refactor while touching the area
   c) Not sure — use default
2) Compatibility target?
   a) **Current project defaults (default)**
   b) Also support older versions: <specify>
   c) Not sure — use default

Reply with: defaults (or 1a 2a)
