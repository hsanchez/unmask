---
status: accepted
---

# Make `VerificationResult` the canonical adherence result

Adherence produced four result shapes (`AuditReport`, `VerificationResult` and two
untyped studio dictionaries), and `audit_judge_case` re-derived the summary,
annotations and limitations the library had already computed. `VerificationResult`
now carries its `VerificationCase` and serializes itself with `to_dict()` to the
studio wire format, which is typed (`AuditEnvelope`, `AdherenceReport`,
`AuditSummary`). The CLI and web API call `audit_judge_case`, a parse, `adherence`,
`to_dict` wrapper. The wire JSON, key order included, is unchanged for all replay
fixtures.

Typing the existing dictionaries and keeping a second conversion path was smaller,
but left two places to update whenever a conclusion changes. The cost is one more
required field on a public dataclass that only `adherence()` constructs; no tagged
release has shipped.
