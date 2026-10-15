---
status: accepted
---

# Keep judge audits separate from guardrail defect analysis

Judge audits use a versioned replay contract and a separate report, while sharing
the studio and source-navigation conventions. Protocol declarations, observable
judge findings, independent grounding and gold labels retain distinct roles.
Satisfiability is established before entailment; unknown facts remain unbound.

The first release adapts the supplied judge-auditor-z3 v0.1.0 source and regression
fixtures. It accepts reviewed annotations with exact evidence spans and preserves
the original core report inside a source-aware Unmask envelope. This makes replay
cases portable and keeps solver conclusions conditional on their formalization.
Live extraction and automatic repair integration remain separate future work.

Imported sources are read-only. Replacing a case clears its report; case hashes
bind annotations and source text to the auditor/schema versions. Imported report
results require a fresh run. These boundaries prevent an older proof or generated
interpretation from silently becoming evidence for a different case.
