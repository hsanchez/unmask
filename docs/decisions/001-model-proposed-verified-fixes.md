---
status: accepted
---

# Separate fix generation from verification

Models propose structured replacements for affected guardrail rules. Unmask applies
those replacements to the original source and displays a patch only after the complete
candidate passes deterministic syntax, trusted-example, regression, and supported-
finding checks. Passing these checks establishes behavior only for the supported
finding classes and supplied labeled cases; it is not a general safety proof.

The model can infer a likely intended edit from policy prose and examples, while Z3
can prove properties only of a concrete symbolic candidate. Accepting model-authored
diffs would blur that boundary and could expose unparsed or unrelated changes. The
service therefore computes diffs itself, never exposes an unvalidated candidate, and
keeps patch application and undo outside the initial workflow.

Natural-language verification is relative to two stable extractions and supplied
trusted examples. Regex verification executes the candidate directly. Proposed-fix
generation uses the selected external provider even when the underlying regex analysis
runs locally.
