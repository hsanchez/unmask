"""Render diagnosis reports as standalone interactive HTML."""

from __future__ import annotations

import html
import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

if TYPE_CHECKING:
  from collections.abc import Sequence


def _mapping(raw: object, name: str) -> dict[str, object]:
  if not isinstance(raw, dict):
    raise TypeError(f"{name} must be a mapping")
  return cast("dict[str, object]", raw)


def _list(raw: object, name: str) -> list[object]:
  if not isinstance(raw, list):
    raise TypeError(f"{name} must be a list")
  return cast("list[object]", raw)


def _text(raw: object, name: str) -> str:
  if not isinstance(raw, str):
    raise TypeError(f"{name} must be a string")
  return raw


@dataclass(frozen=True)
class VisualFinding:
  """A diagnosis finding flattened into the fields the HTML template needs."""

  id: str
  bug_type: str
  severity: str
  summary: str
  explanation: str
  remediation: str
  rule_ids: tuple[str, ...]
  formula: str
  witness: dict[str, bool]
  what_matched: str
  counterexample: str
  why_violation: str
  expected_reason: str
  expected_outcome_source: str
  rule_evidence: tuple[RuleEvidence, ...] = ()


@dataclass(frozen=True)
class RuleEvidence:
  """One regex match rendered in a finding's evidence list."""

  rule_id: str
  action: str
  pattern: str
  matched_text: str
  start: int
  end: int

  def to_dict(self) -> dict[str, object]:
    return {
      "ruleId": self.rule_id,
      "action": self.action,
      "pattern": self.pattern,
      "matchedText": self.matched_text,
      "start": self.start,
      "end": self.end,
    }


@dataclass(frozen=True)
class Highlight:
  """A character span in the incident text to mark up, linked to its finding."""

  start: int
  end: int
  matched_text: str
  rule_id: str
  action: str
  pattern: str
  finding: VisualFinding


class CollectionResult(TypedDict):
  """One row of a rendered `render_collection_index` table."""

  incident: int
  sourceLocation: int
  value: str
  findings: int
  status: str
  expectedOutcomeSource: str
  expectedReason: str | None
  config: str
  report: str
  visualization: str | None


class DiagnosisVisualizer:
  """Build an offline HTML explanation from a diagnosis report."""

  def render_file(self, report_path: str | Path) -> str:
    """Load a diagnosis report JSON file and render it. See `render`."""
    source: Path = Path(report_path)
    raw = cast("object", json.loads(s=source.read_text(encoding="utf-8")))
    return self.render(raw)

  def render(self, raw: object) -> str:
    """Render a diagnosis report dict as a standalone HTML page.

    Raises:
      TypeError: if `raw`, `report.incident`, or a nested field has the wrong shape.
    """
    report: dict[str, object] = _mapping(raw, name="diagnosis report")
    incident: dict[str, object] = _mapping(raw=report.get("incident"), name="incident")
    text: str = _text(raw=incident.get("text"), name="incident.text")
    if not _list(raw=report.get("findings"), name="findings"):
      return self._render_unproven(report, incident, text)
    findings: tuple[VisualFinding, ...] = self._findings(report)
    highlights: tuple[Highlight, ...] = tuple(
      highlight for finding in findings for highlight in self._highlights(finding)
    )
    segments: str = self._segments(text, highlights)
    cards: str = "".join(self._finding_card(finding) for finding in findings)
    data: str = json.dumps(
      obj={
        finding.id: {
          "summary": finding.summary,
          "explanation": finding.explanation,
          "remediation": finding.remediation,
          "formula": finding.formula,
          "witness": finding.witness,
          "whatMatched": finding.what_matched,
          "counterexample": finding.counterexample,
          "whyViolation": finding.why_violation,
          "expectedReason": finding.expected_reason,
          "expectedOutcomeSource": finding.expected_outcome_source,
          "rules": [item.to_dict() for item in finding.rule_evidence],
        }
        for finding in findings
      }
    ).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(s=_text(raw=report.get("name"), name="name"))} diagnosis</title>
<style>
:root {{ color-scheme: light; font-family: system-ui, sans-serif; }}
body {{ margin: 0; background: #f6f8fa; color: #1f2328; }}
main {{ max-width: 1100px; margin: 0 auto; padding: 32px 20px; }}
h1 {{ margin-top: 0; }}
.panel {{ background: white; border: 1px solid #d0d7de; border-radius: 8px;
  padding: 20px; margin: 16px 0; box-shadow: 0 1px 2px #0000000a; }}
.agent-output {{ font: 15px/1.8 ui-monospace, monospace; white-space: pre-wrap;
  overflow-wrap: anywhere; }}
.violation {{ border: 0; border-radius: 3px; padding: 2px 1px; cursor: pointer;
  color: inherit; font: inherit; text-align: inherit; }}
.over_strict {{ background: #ffebe9; box-shadow: inset 0 -3px #cf222e; }}
.contradiction {{ background: #fff8c5; box-shadow: inset 0 -3px #bf8700; }}
.ambiguity {{ background: #ddf4ff; box-shadow: inset 0 -3px #0969da; }}
.violation:hover, .violation:focus {{ outline: 2px solid #0969da; }}
.details {{ min-height: 150px; }}
.finding {{ border-left: 5px solid #57606a; }}
.finding[data-type="over_strict"] {{ border-color: #cf222e; }}
.finding[data-type="contradiction"] {{ border-color: #bf8700; }}
.finding[data-type="ambiguity"] {{ border-color: #0969da; }}
.meta {{ color: #57606a; font-size: 13px; }}
code, pre {{ background: #f6f8fa; border-radius: 4px; }}
code {{ padding: 2px 4px; }}
pre {{ padding: 12px; overflow: auto; white-space: pre-wrap; }}
.label {{ display: inline-block; border-radius: 10px; padding: 2px 8px;
  background: #eaeeef; font-size: 12px; font-weight: 600; }}
.rule-box {{ background: #fff8c5; border: 1px solid #d4a72c;
  border-radius: 6px; padding: 10px 12px; margin: 8px 0; }}
</style>
</head>
<body>
<main>
<h1>{html.escape(s=_text(raw=report.get("name"), name="name"))}</h1>
<p class="meta">Hover, focus, or select a highlighted violation to inspect its
regex evidence and Z3-backed explanation.</p>
<section class="panel agent-output" aria-label="Agent output">{segments}</section>
<section id="details" class="panel details" aria-live="polite">
<strong>Select a highlighted span.</strong>
</section>
<section aria-label="All findings">{cards}</section>
</main>
<script>
const findingData = {data};
const details = document.getElementById("details");
function showFinding(id) {{
  const item = findingData[id];
  if (!item) return;
  const rules = item.rules.map((rule) =>
    "<div class='rule-box'><strong>Faulty rule: " +
    escapeHtml(rule.ruleId) + "</strong>" +
    "<div>Action: <code>" + escapeHtml(rule.action) + "</code></div>" +
    "<div>Regex: <code>" + escapeHtml(rule.pattern) + "</code></div>" +
    "<div>Matched: <code>" + escapeHtml(rule.matchedText) + "</code> at [" +
    rule.start + ", " + rule.end + ")</div>" +
    "</div>"
  ).join("");
  details.innerHTML =
    "<h2>" + escapeHtml(item.summary) + "</h2>" +
    "<h3>Faulty guardrail rule</h3>" + rules +
    "<h3>What matched</h3><p>" + escapeHtml(item.whatMatched) + "</p>" +
    "<h3>Counterexample</h3><p>" + escapeHtml(item.counterexample) + "</p>" +
    "<h3>Why this is a violation</h3><p>" +
    escapeHtml(item.whyViolation) + "</p>" +
    "<h3>Expected-outcome evidence</h3><p>" +
    escapeHtml(item.expectedReason) + "</p>" +
    "<p class='meta'>Label source: " +
    escapeHtml(item.expectedOutcomeSource) + "</p>" +
    "<h3>Recommended fix</h3><p>" + escapeHtml(item.remediation) + "</p>" +
    "<details><summary>Technical Z3 details</summary>" +
    "<h3>Formula</h3><pre>" + escapeHtml(item.formula) + "</pre>" +
    "<h3>Predicate assignment</h3><pre>" +
    escapeHtml(JSON.stringify(item.witness, null, 2)) + "</pre></details>";
}}
function escapeHtml(value) {{
  const node = document.createElement("div");
  node.textContent = String(value);
  return node.innerHTML;
}}
document.querySelectorAll(".violation").forEach((node) => {{
  node.addEventListener("mouseenter", () => showFinding(node.dataset.finding));
  node.addEventListener("focus", () => showFinding(node.dataset.finding));
  node.addEventListener("click", () => showFinding(node.dataset.finding));
}});
</script>
</body>
</html>
"""

  def _findings(self, report: dict[str, object]) -> tuple[VisualFinding, ...]:
    result: list[VisualFinding] = []
    for index, raw_finding in enumerate(
      _list(raw=report.get("findings"), name="findings")
    ):
      name: str = f"findings[{index}]"
      finding: dict[str, object] = _mapping(raw=raw_finding, name=name)
      metadata: dict[str, object] = _mapping(
        raw=finding.get("metadata"), name=f"{name}.metadata"
      )
      proof: dict[str, object] = _mapping(
        raw=finding.get("proof"), name=f"{name}.proof"
      )
      counterexample = finding.get("counterexampleExplanation")
      explanation_details: dict[str, object] = (
        _mapping(raw=counterexample, name=f"{name}.counterexampleExplanation")
        if counterexample is not None
        else {}
      )
      raw_witness: dict[str, object] = _mapping(
        raw=proof.get("witness"), name=f"{name}.proof.witness"
      )
      witness: dict[str, bool] = {}
      for key, value in raw_witness.items():
        if not isinstance(value, bool):
          raise TypeError(f"{name}.proof.witness must map strings to booleans")
        witness[key] = value
      raw_rule_ids: list[object] = _list(
        raw=finding.get("ruleIds"), name=f"{name}.ruleIds"
      )
      rule_ids: tuple[str, ...] = tuple(
        _text(raw=item, name=f"{name}.ruleIds") for item in raw_rule_ids
      )
      evidence: tuple[RuleEvidence, ...] = tuple(
        self._rule_evidence(
          evidence=_mapping(raw=item, name=f"{name}.evidence"),
          name=f"{name}.evidence[{item_index}]",
        )
        for item_index, item in enumerate(
          _list(raw=finding.get("evidence"), name=f"{name}.evidence")
        )
      )
      result.append(
        VisualFinding(
          id=_text(raw=finding.get("id"), name=f"{name}.id"),
          bug_type=_text(raw=finding.get("type"), name=f"{name}.type"),
          severity=_text(raw=finding.get("severity"), name=f"{name}.severity"),
          summary=_text(raw=finding.get("summary"), name=f"{name}.summary"),
          explanation=_text(raw=finding.get("explanation"), name=f"{name}.explanation"),
          remediation=_text(raw=finding.get("remediation"), name=f"{name}.remediation"),
          rule_ids=rule_ids,
          formula=_text(raw=proof.get("formula"), name=f"{name}.proof.formula"),
          witness=witness,
          what_matched=str(
            explanation_details.get(
              "whatMatched",
              finding.get("explanation", ""),
            )
          ),
          counterexample=str(
            explanation_details.get(
              "counterexample",
              finding.get("explanation", ""),
            )
          ),
          why_violation=str(
            explanation_details.get(
              "whyViolation",
              finding.get("explanation", ""),
            )
          ),
          expected_reason=str(metadata.get("expectedReason", "")),
          expected_outcome_source=str(metadata.get("expectedOutcomeSource", "missing")),
          rule_evidence=evidence,
        )
      )
    if not result:
      raise ValueError("diagnosis report contains no findings to visualize")
    return tuple(result)

  @staticmethod
  def _rule_evidence(evidence: dict[str, object], name: str) -> RuleEvidence:
    location: dict[str, object] = _mapping(
      raw=evidence.get("location"), name=f"{name}.location"
    )
    start = location.get("start")
    end = location.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
      raise TypeError(f"{name}.location start and end must be integers")
    return RuleEvidence(
      rule_id=_text(raw=evidence.get("ruleId"), name=f"{name}.ruleId"),
      action=_text(raw=evidence.get("action"), name=f"{name}.action"),
      pattern=_text(raw=evidence.get("pattern"), name=f"{name}.pattern"),
      matched_text=_text(raw=evidence.get("matchedText"), name=f"{name}.matchedText"),
      start=start,
      end=end,
    )

  @staticmethod
  def _highlights(finding: VisualFinding) -> tuple[Highlight, ...]:
    return tuple(
      Highlight(
        start=evidence.start,
        end=evidence.end,
        matched_text=evidence.matched_text,
        rule_id=evidence.rule_id,
        action=evidence.action,
        pattern=evidence.pattern,
        finding=finding,
      )
      for evidence in finding.rule_evidence
    )

  def _segments(self, text: str, highlights: tuple[Highlight, ...]) -> str:
    boundaries: list[int] = sorted(
      {
        0,
        len(text),
        *(item.start for item in highlights),
        *(item.end for item in highlights),
      }
    )
    if boundaries[0] < 0 or boundaries[-1] > len(text):
      raise ValueError("finding evidence location exceeds incident text")
    parts: list[str] = []
    for start, end in itertools.pairwise(boundaries):
      segment: str = text[start:end]
      active: list[Highlight] = [
        item for item in highlights if item.start <= start and item.end >= end
      ]
      if not active:
        parts.append(html.escape(s=segment))
        continue
      primary: Highlight = active[0]
      title: str = "\n".join(
        f"[{item.finding.summary} | {item.rule_id} | {item.action}]" for item in active
      )
      classes: str = " ".join(sorted({item.finding.bug_type for item in active}))
      parts.append(
        f'<button type="button" class="violation {html.escape(s=classes)}" '
        + f'data-finding="{html.escape(s=primary.finding.id)}" '
        + f'title="{html.escape(s=title)}">{html.escape(s=segment)}</button>'
      )
    return "".join(parts)

  @staticmethod
  def _finding_card(finding: VisualFinding) -> str:
    witness: str = html.escape(
      s=json.dumps(obj=finding.witness, indent=2, sort_keys=True)
    )
    rule_details: str = "".join(
      f"""<div class="rule-box">
<strong>Faulty rule: {html.escape(s=evidence.rule_id)}</strong>
<div>Action: <code>{html.escape(s=evidence.action)}</code></div>
<div>Regex: <code>{html.escape(s=evidence.pattern)}</code></div>
<div>Matched: <code>{html.escape(s=evidence.matched_text)}</code>
at [{evidence.start}, {evidence.end})</div>
</div>"""
      for evidence in finding.rule_evidence
    )
    return f"""<article class="panel finding" data-type="{html.escape(s=finding.bug_type)}">
<span class="label">{html.escape(s=finding.bug_type)}</span>
<h2>{html.escape(s=finding.summary)}</h2>
<h3>Faulty guardrail rule</h3>{rule_details}
<h3>What matched</h3><p>{html.escape(s=finding.what_matched)}</p>
<h3>Counterexample</h3><p>{html.escape(s=finding.counterexample)}</p>
<h3>Why this is a violation</h3><p>{html.escape(s=finding.why_violation)}</p>
<h3>Expected-outcome evidence</h3>
<p>{html.escape(s=finding.expected_reason)}</p>
<p class="meta">Label source:
{html.escape(s=finding.expected_outcome_source)}</p>
<p class="meta">Rules: {html.escape(s=", ".join(finding.rule_ids))} &middot;
Severity: {html.escape(s=finding.severity)}</p>
<details><summary>Technical Z3 details</summary>
<h3>Formula</h3><pre>{html.escape(s=finding.formula)}</pre>
<h3>Predicate assignment</h3><pre>{witness}</pre>
</details>
</article>"""

  @staticmethod
  def _render_unproven(
    report: dict[str, object], incident: dict[str, object], text: str
  ) -> str:
    matches: list[object] = _list(raw=report.get("matches"), name="matches")
    skipped: list[object] = _list(
      raw=report.get("skippedAnalyses"), name="skippedAnalyses"
    )
    match_items: str = "".join(
      f"<li><code>{html.escape(s=str(_mapping(raw=item, name='match').get('ruleId')))}</code>"
      + f" matched <code>{html.escape(s=str(_mapping(raw=item, name='match').get('matchedText')))}</code></li>"
      for item in matches
    )
    reasons: str = "".join(
      f"<li>{html.escape(s=str(_mapping(raw=item, name='skipped analysis').get('reason')))}</li>"
      for item in skipped
    )
    expected_reason = incident.get("expectedReason")
    expected_source = incident.get("expectedOutcomeSource", "missing")
    outcome: str = (
      "The guardrail match was reproduced, but Bugfinder could not prove "
      + "over-strictness, contradiction, or ambiguity from the supplied evidence."
      if matches
      else "No regex matched the supplied incident location, so Bugfinder "
      + "could not test that incident for a supported defect."
    )
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(s=_text(raw=report.get("name"), name="name"))} diagnosis</title>
<style>body {{ max-width: 900px; margin: 32px auto; padding: 0 20px;
font-family: system-ui, sans-serif; }} pre {{ white-space: pre-wrap;
background: #f6f8fa; padding: 16px; }} code {{ background: #f6f8fa; }}</style>
</head><body>
<h1>{html.escape(s=_text(raw=report.get("name"), name="name"))}</h1>
<h2>No supported defect proven</h2>
<p>{outcome}</p>
<h3>Agent output</h3><pre>{html.escape(s=text)}</pre>
<h3>Exact regex matches</h3><ul>{match_items or "<li>None</li>"}</ul>
<h3>Expected-outcome evidence</h3>
<p>{html.escape(s=str(expected_reason or "Not supplied"))}</p>
<p>Label source: {html.escape(s=str(expected_source))}</p>
<h3>Why analysis was limited</h3><ul>{reasons or "<li>No supported defect was satisfiable.</li>"}</ul>
</body></html>
"""


def write_visualization(path: str | Path, content: str) -> Path:
  """Write rendered HTML `content` to `path`, creating parent directories as needed."""
  output: Path = Path(path)
  output.parent.mkdir(parents=True, exist_ok=True)
  _ = output.write_text(data=content, encoding="utf-8")
  return output


def _collection_row(result: CollectionResult) -> str:
  value: str = html.escape(s=result["value"])
  visualization: str | None = result["visualization"]
  if visualization is not None:
    visualization_path: Path = Path(visualization)
    relative_link: str = f"{visualization_path.parent.name}/{visualization_path.name}"
    report_cell: str = f'<a href="{html.escape(s=relative_link)}">Open report</a>'
  else:
    report_cell = "Unavailable"
  return (
    "<tr>"
    f"<td>{result['incident']}</td>"
    f"<td>{result['sourceLocation']}</td>"
    f"<td><code>{value}</code></td>"
    f"<td>{result['findings']}</td>"
    f"<td>{html.escape(s=result['status'])}</td>"
    f"<td>{html.escape(s=result['expectedOutcomeSource'])}</td>"
    f"<td>{report_cell}</td>"
    "</tr>"
  )


def render_collection_index(results: Sequence[CollectionResult]) -> str:
  """Render links to all visual reports in a diagnosed collection."""
  rows: str = "".join(_collection_row(result) for result in results)
  return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bugfinder diagnosis collection</title>
<style>
body {{ max-width: 1000px; margin: 32px auto; padding: 0 20px;
  font-family: system-ui, sans-serif; color: #1f2328; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ border: 1px solid #d0d7de; padding: 8px 10px; text-align: left; }}
th {{ background: #f6f8fa; }}
code {{ overflow-wrap: anywhere; }}
</style>
</head>
<body>
<h1>Bugfinder diagnosis collection</h1>
<p>Select a violation to view its faulty rule, natural-language counterexample
explanation, remediation, and technical Z3 details.</p>
<table>
<thead><tr><th>Incident</th><th>Source location</th><th>Value</th>
<th>Findings</th><th>Status</th><th>Label source</th><th>Report</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body>
</html>
"""
