This is my understanding of what unmask should do.

Unmask detects (as of now but in the future more) three classes
of defects in natural language specifications such as guardrails,
rubrics, etc.

1. over strict rules: a blocking rule can apply to a semantically safe request.
2. contradiction: allow and block rules can apply to the same request.
3. ambigyity: two plausible interpretations of an unclear directive produce different applicability or decisions.

Unmask focuses on policy analysis rather than conversation synthesis. An LLM
translates natural language into a shared boolean model; Z3 then proves overlaps
or intrepretation divergence and emits concrete predicate assignments as witnesses.
Ambiguity findings retain the natural extractor's quoted evidence and rationale.


The expected workflow has two primarty commands:
1. scan: scan proactively finds specification detects in NL or regex guardrails.
2. debug: debug replays reported regex violations and explains defects demonstrated by those incidents.
Scan existing natural language (NL) specifications with an optional taxonomy.

e.g., scan
uv run unmask scan examples/guardrails.yaml \
   --taxonomy examples/taxonomy.yaml \
   --output output/scan

uv run unmask scan examples/regexes.yaml --output/scan

e.g., debug
uv run unmask debug examples/regexes.yaml \
   examples/violations.json \
   --output output/debug
   --open


e.g., violations.json

```json
{
  "violations": [
    {
      "context": "Anthropic will invest up to $2 Billion.",
      "location": "1098",
      "value": "will invest",
      "expected_outcome": "allow",
      "expected_reason": "This reports a third party...",
    }
  ]
}
```

e.g., regexes.yaml

```yaml
schema_version: 1
kind: regex
name: some_name
engine: python_re
scope: agent_output
match_timeout_seconds: 1.0

rules:
  - id: 
    action: block
    pattern: '\b(?:buy|sell|invest)\b'
    flags:
      - IGNORECASE
    description: Detect ...
  - id:...

examples:
  - content: Anthropic will invest up to ....
    expected_action: allow
    reason: ....
  - content: ....
    expected_action: ...
```

e.g., taxonomy.yaml
```yaml
intent_classes:
  Allowed:
    description: .....
  Blocked:
    description: .....
```

e.g., guardrail.yaml
```yaml
name: NAME
system_prompt: >-
  .............
rules:
  - rule 1
  - rule 2
  - ....
examples:
  - content: ....
    should_block: false
    reasoning: ....
  - content: ....
    should_block: true
```

e.g., Symbolic semantics

Conditions use disjunctive normal form. 
Some of the conditions checked:

- safe(x) AND blocked(x) for over strictness
- allowed(x) and blocked(x) for contradictions
- interpretationA(x) OR interpretationB(x) when both readings have the 
same decision, or decision/applicability divergence when their decisions differ.
