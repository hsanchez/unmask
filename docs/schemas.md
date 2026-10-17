# Input schemas and semantics

Formats accepted by `unmask scan`, `unmask diagnose`, `unmask adherence`, the
studio, and the Python library.

## Natural-language guardrail

```yaml
name: financial-information-policy
system_prompt: >-
  Provide accurate financial education and summaries of public company
  announcements without giving personalized investment advice.
rules:
  - Block any response containing the words buy, sell, or invest.
  - Allow general financial education that does not recommend a specific transaction.
  - Block instructions explaining how to trade a specific investment.
  - Block personalized investment recommendations for minors and first-time investors.
examples:
  - content: Anthropic will invest up to $2 billion.
    should_block: false
    reasoning: Neutral reporting about a third-party company action is allowed.
```

Every rule is a non-empty string. Every example requires non-empty `content` and Boolean `should_block`.

## Regex manifest

```yaml
schema_version: 1
kind: regex
name: financial-information-policy
engine: python_re
scope: agent_output
match_timeout_seconds: 1.0

rules:
  - id: block-investment-term
    action: block
    pattern: '\binvest\b'
    flags: [IGNORECASE]
    description: Block any use of the word invest.
  - id: first-time-investor-scope
    action: block
    pattern: '\A(?=.*\bfirst-time investor\b)(?=.*\bbuy\b).*\Z'
    flags: [IGNORECASE, DOTALL]
    description: Block personalized recommendations for minors and first-time investors.
    interpretations:
      - label: either protected audience
        action: block
        pattern: '\A(?=.*\b(?:minor|first-time investor)\b)(?=.*\bbuy\b).*\Z'
      - label: both protected audiences
        action: block
        pattern: '\A(?=.*\bminor\b)(?=.*\bfirst-time investor\b)(?=.*\bbuy\b).*\Z'

examples:
  - content: Anthropic will invest up to $2 billion.
    expected_action: allow
    reason: Neutral reporting about a third-party company action is allowed.
```

Regex ambiguity analysis requires exactly two explicit `interpretations` on a rule. Regex contradiction analysis requires both allow and block rules. Over-strictness requires a trusted expected `allow` action and policy reason.

## Violations

```json
{
  "violations": [
    {
      "context": "Anthropic will invest up to $2 billion.",
      "location": 1842,
      "value": "invest",
      "expected_outcome": "allow",
      "expected_reason": "Neutral reporting about a third-party company action is allowed."
    }
  ]
}
```

`location` is a non-negative source offset. `value` must occur exactly once in `context`. `expected_outcome` and `expected_reason` are optional but must be supplied together.

## Taxonomy

Natural-language scans pass the complete YAML document to the semantic extractor as context:

```yaml
intent_classes:
  NeutralThirdPartyReporting:
    expected_action: allow
    description: Factual reporting about an action taken by a named third party.
  GeneralFinancialEducation:
    expected_action: allow
    description: Explanations of financial concepts without a recommendation to transact.
  PersonalizedInvestmentRecommendation:
    expected_action: block
    description: Advice directing a person to transact in a specific asset.
```

Regex scans consume versioned labeled examples from the same optional file:

```yaml
regex_examples_version: 1
regex_examples:
  - content: Summarize how index funds spread investment risk.
    expected_action: allow
    reason: This is general financial education and does not recommend a transaction.
```

Both sections may coexist in one taxonomy document.

## Symbolic semantics

Conditions use disjunctive normal form: the outer list is OR and each inner list is AND.

- Over-strictness: `safe(x) AND blocked(x)`
- Contradiction: `allowed(x) AND blocked(x)`
- Same-decision ambiguity: `XOR(interpretationA(x), interpretationB(x))`
- Opposite-decision ambiguity: `interpretationA(x) OR interpretationB(x)`

Natural-language ambiguity findings retain the source excerpt, ambiguity rationale, both interpretation labels and decisions, and both interpretation rationales. The accompanying finding rationale explains the Z3 proof.

## Judge replay

The version-1 replay schema is demonstrated by
[`judge-audit.json`](src/unmask/web/samples/judge-audit.json). It includes
`schema_version`, `id`, `context`, `response`, `rationale`, `verdict`, `protocol`,
`findings`, `extraction`, optional `grounding_pairs` and optional `gold_verdict`.
Atoms have judge, context or procedure roles; expressions support Boolean
constants, atoms, and/or/not/implies/iff. TRUE/FALSE findings need exact evidence
spans; UNKNOWN findings may omit evidence. Evidence offsets use Unicode code
points and half-open ranges in the original source. The parser rejects unknown
fields, duplicate JSON keys, undeclared atoms and incompatible evidence roles.
Limits: 1 MB case input in the CLI, 128 atoms, 1,024 expression nodes and depth 16.
The web API caps the complete JSON request at 1 MB, including its wrapper and
escaping, so its effective case limit is lower. Each audit
uses a separate Z3 context, a five-second solver budget and one-second query
limits. Exhausted budgets produce solver-unknown results.
The server accepts one audit at a time per process. Cancelling stops the browser
from waiting; the current solver run finishes within its budget before releasing
the slot.
