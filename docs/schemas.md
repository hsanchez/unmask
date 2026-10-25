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

## Deterministic JSON observation

`unmask.judge.check_json_object(response, atom="context_json")` returns an
`Annotation` for a declared context atom. It checks the entire response as JSON,
requires an object at the top level, and records `json-object/v2` plus an exact
response span. Pair that atom with a supplied judge assessment through ordinary
`VerificationCase.grounding_pairs`; the existing Z3 engine checks their agreement.

The checker accepts JSON whitespace and duplicate object keys. It checks syntax
and top-level type, with no schema, required-field, or key-uniqueness requirement.
Escaped lone surrogates are accepted as JSON syntax; this does not promise
interoperability with stricter Unicode consumers.
Numbers are parsed without conversion to machine integers/floats, so large valid
numbers remain valid; NaN and Infinity are rejected. Markdown fences and trailing
text are invalid. A response whose first non-JSON-whitespace character is not
`{` fails immediately. At nesting depth 64, the standard parser checks a bounded
prefix with the unexamined subtree replaced by a placeholder and containers
closed. Definite prefix syntax errors return FALSE. A valid prefix returns
UNKNOWN: errors beyond that point may remain undetected, and deep valid objects
are not declared invalid. Parser nesting exhaustion also returns UNKNOWN.
Invalid source types, empty/whitespace-only sources, invalid Unicode, and sources
over 1 MB raise `ValidationError`, following the replay source contract. The
complete replay must still fit its aggregate byte limit. The enclosing case
validates the atom's declaration, identifier, and evidence role.

This creates an objective observation; it does not extract a judge's interpretation
or prove that a natural-language instruction was translated correctly.

## Typed claim inputs

`unmask.claims.ClaimCase` is a manually reviewed authoring layer for one fixed
protocol: identify factual response claims, explicitly address each, compare
judge support with independent context support, and PASS iff every factual response claim
is judged supported. It compiles finite obligations into the existing replay
schema and solver. Arbitrary protocols still use `VerificationCase` directly.

Use `Message` for identified source ranges and `Claim` for ranges inside response
messages. Reuse `unmask.judge.Evidence` and `Truth`. All offsets remain half-open
Unicode code-point ranges in the original context, response, or rationale;
normalization and source flattening are never performed.

`RelationReview` supplies the assessment, message scope, exact evidence, and
explanation for one claim:

| Relation | Evidence source | Meaning |
| --- | --- | --- |
| `ADDRESSES` | rationale | Judge explicitly addresses the claim |
| `JUDGES_SUPPORTED` | rationale | Judge labels the claim supported |
| `SUPPORTS` | context | Independent reviewer finds context support |
| `CONTRADICTS` | context | Independent reviewer finds a contradiction |

Missing reviews and `Truth.UNKNOWN` leave facts unconstrained. A TRUE contradiction
establishes lack of support; a FALSE contradiction does not establish support.
A FALSE support assessment must review all declared context messages covering
the entire context, rather than infer global absence from a partial search.
Similarly, FALSE addressing requires review of the entire rationale. A FALSE
`JUDGES_SUPPORTED` review means an explicit unsupported judgment; silence remains
UNKNOWN.
Conflicting support assessments remain inconsistent inputs. A correct FAIL on
an unsupported response can satisfy adherence: the judge's correctness is the
subject, rather than the candidate response's quality.

An optional `InventoryReview` states completeness over response messages. TRUE
requires all declared response messages to cover the entire response. Omitted
or unknown completeness prevents global satisfaction and keeps unlisted judgments
unknown: positive labels on a partial inventory cannot force PASS. Empty claim
inventories cannot establish satisfaction. Explicit violations remain detectable alongside unrelated
unknowns. The compiler validates scope and quotations; reviewers remain
responsible for semantic truth, claim inventory completeness, and translation
faithfulness. Matching quotations alone proves none of these.

IDs are globally distinct ASCII identifiers, up to 48 characters; record fields
use immutable tuples. Expansion is bounded before atom creation: 42 claims
(three atoms each plus inventory and, when incomplete, an unknown remainder),
128 messages, and 255 relation reviews.
Each review has at most 16 evidence spans. Typed inputs, serialized artifacts,
and compiled replays each obey the existing 1,000,000-byte limit. Invalid inputs
raise `ValidationError`.

`compiled.case` is an ordinary `VerificationCase`: existing adherence, benchmark,
CLI, and studio paths accept its replay JSON without changes. Stable claim IDs
produce stable atom and requirement IDs, independent of record order or added
claims. `compiled.claims_for(finding)` maps check atoms and conflicting annotations
back to typed claims. For an inconsistency, `finding.conflicting_annotations`
contains the original annotations selected by the solver's UNSAT core, including
their quotations and explanations. A core is sufficient evidence of a conflict;
it need not be minimal or include every conflicting review. Inventory findings
and protocol conflicts without annotations have no claim attribution.

`compiled.review_gaps` lists missing or unresolved addressing, judge-support,
independent-support, and inventory assessments, using stable atom IDs as subjects.
Each gap carries its claim when applicable. Gaps are separate from violation
findings and can coexist with detected violations. An incomplete inventory gap
cannot identify claims that were never supplied. The fixed protocol requires a
complete reviewed inventory: an explicit FALSE inventory assessment refutes that
obligation, but does not establish that the judge omitted a claim in its reasoning.

`CompiledClaims.to_json()` stores typed inputs and bindings in a separate
versioned artifact (`schema_version: 1`, `compiler: claims-v1`). Reload with
`CompiledClaims.from_json(text)`, which recompiles and checks binding consistency.
The studio accepts `json.dumps(compiled.case.to_dict())`, rather than the typed
artifact. Artifacts are supplied assertions, not authenticated review records.
Binding validation checks internal consistency, rather than a signature or
content integrity. Authenticate reviews out of band when accepting contributed
artifacts. The compiler's `VALID` translation status describes the fixed protocol
lowering; semantic labels and completeness remain unauthenticated caller assertions.
Changes to generated IDs or lowering semantics require a new compiler version;
`claims-v1` artifacts retain their current interpretation and identifier contract.
Persist ordinary replay JSON when only the solver input is needed.
