"use strict";

function codePointToCodeUnit(text, index) {
  return Array.from(text).slice(0, index).join("").length;
}

function renderSource(documentData, findings) {
  const source = documentData.rawText;
  elements.sourceView.dataset.documentId = documentData.id;
  const references = [];
  for (const finding of findings) {
    for (const reference of finding.sourceReferences || []) {
      if (reference.documentId !== documentData.id) continue;
      references.push({
        start: codePointToCodeUnit(source, reference.start),
        end: codePointToCodeUnit(source, reference.end),
        findingId: finding.id,
      });
    }
  }
  const boundaries = [...new Set([0, source.length, ...references.flatMap((item) => [item.start, item.end])])]
    .filter((value) => value >= 0 && value <= source.length)
    .sort((left, right) => left - right);
  const nodes = [];
  for (let index = 0; index < boundaries.length - 1; index += 1) {
    const start = boundaries[index];
    const end = boundaries[index + 1];
    if (end <= start) continue;
    const findingIds = [...new Set(references
      .filter((reference) => reference.start <= start && reference.end >= end)
      .map((reference) => reference.findingId))];
    const span = document.createElement("span");
    span.textContent = source.slice(start, end);
    if (findingIds.length) {
      span.className = "source-reference";
      span.tabIndex = 0;
      span.dataset.findingIds = JSON.stringify(findingIds);
      span.dataset.sourceStart = String(start);
      span.dataset.sourceEnd = String(end);
      span.setAttribute("aria-label", `${findingIds.length} related finding${findingIds.length === 1 ? "" : "s"}`);
      bindFindingPreview(span, findingIds);
      span.addEventListener("click", () => pinFindings(findingIds));
      span.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          pinFindings(findingIds);
        }
      });
    }
    nodes.push(span);
  }
  elements.sourceView.replaceChildren(...nodes);
}

function findingCountText(count) {
  return `${count} finding${count === 1 ? "" : "s"}`;
}

function renderFindingMessage(message) {
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent = message;
  elements.findingList.replaceChildren(empty);
}

function renderFindings(run) {
  const findings = run?.findings || [];
  elements.findingCount.textContent = String(findings.length);
  elements.findingCount.setAttribute("aria-label", findingCountText(findings.length));
  elements.mobileFindingCount.textContent = String(findings.length);
  elements.summary.hidden = !run;
  if (run) {
    const status = String(run.summary?.status || "completed").replaceAll("_", " ");
    elements.summary.textContent = `${findingCountText(findings.length)} · ${status}`;
  }
  if (!run) {
    renderFindingMessage("Scan or diagnose this specification to inspect findings.");
    return;
  }
  if (!findings.length) {
    renderFindingMessage("No supported specification defects were found.");
    return;
  }
  elements.findingList.replaceChildren(...findings.map((finding) => findingCard(finding, run)));
  applyHighlights();
}

function proposalForFinding(finding, run) {
  const study = state.activeStudy;
  if (!study) return null;
  return state.proposalResults.get(proposalKey(study, finding.id))
    || run?.proposedFixes?.[finding.id]
    || null;
}

function proposalCheckList(checks) {
  const list = document.createElement("ul");
  list.className = "proposal-checks";
  for (const check of checks || []) {
    const item = document.createElement("li");
    item.className = check.status;
    item.textContent = check.label;
    list.append(item);
  }
  return list;
}

function proposalDiff(patch) {
  const pre = document.createElement("pre");
  pre.className = "proposal-diff";
  for (const line of patch.split(/(?<=\n)/)) {
    const span = document.createElement("span");
    span.textContent = line;
    if (line.startsWith("+") && !line.startsWith("+++")) span.className = "diff-addition";
    else if (line.startsWith("-") && !line.startsWith("---")) span.className = "diff-removal";
    pre.append(span);
  }
  return pre;
}

function stopFindingNavigation(event) {
  event.stopPropagation();
}

function proposedFixSection(finding, run) {
  const study = state.activeStudy;
  if (!study) return null;
  const proposal = proposalForFinding(finding, run);
  if (!proposal) return null;
  const section = document.createElement("section");
  section.className = "proposed-fix";
  const heading = document.createElement("strong");
  heading.className = "finding-detail-label";
  heading.textContent = proposal.status === "validated" ? "Checks passed" : "Fix unavailable";
  section.append(heading);
  if (proposal?.status === "validated") {
    if (proposal.explanation) {
      const explanation = document.createElement("p");
      explanation.textContent = proposal.explanation;
      section.append(explanation);
    }
    section.append(proposalDiff(proposal.unifiedDiff), proposalCheckList(proposal.validationChecks));
    const attribution = document.createElement("small");
    attribution.className = "proposal-attribution";
    attribution.textContent = `Proposed by ${proposal.provider} · ${proposal.model}; checked by Unmask against supplied cases`;
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "proposal-button secondary";
    copy.textContent = "Copy patch";
    copy.addEventListener("click", async (event) => {
      stopFindingNavigation(event);
      try {
        await copyText(proposal.unifiedDiff);
        copy.textContent = "Copied";
        window.setTimeout(() => { copy.textContent = "Copy patch"; }, 1800);
      } catch (error) {
        setNotice(error.message);
      }
    });
    copy.addEventListener("keydown", stopFindingNavigation);
    section.append(attribution, copy);
    return section;
  }
  const message = document.createElement("p");
  message.textContent = "No sufficiently supported automatic fix is available.";
  section.append(message, proposalCheckList(proposal.validationChecks));
  return section;
}

function generateFixAction(finding, run) {
  const study = state.activeStudy;
  if (!study || proposalForFinding(finding, run)) return null;
  const key = proposalKey(study, finding.id);
  const controller = state.proposalControllers.get(key);
  const generate = document.createElement("button");
  generate.type = "button";
  generate.className = "generate-fix-action";
  generate.textContent = controller ? "Cancel" : "Generate fix →";
  generate.addEventListener("click", (event) => {
    stopFindingNavigation(event);
    generateProposedFix(finding, run);
  });
  generate.addEventListener("keydown", stopFindingNavigation);
  return generate;
}

function findingHeadline(finding) {
  if (finding.type === "ambiguity") return "Interpretations disagree.";
  if (finding.type === "contradiction") return "Rules require conflicting outcomes.";
  if (finding.type === "over_strict") return "An allowed case is blocked.";
  return finding.summary;
}

function findingOrigin(finding, run) {
  const workflow = run?.workflow === "diagnose" ? "Diagnose" : "Scan";
  const content = finding.metadata?.exampleContent;
  if (typeof content !== "string" || !content.trim()) return workflow;
  const normalized = content.replaceAll(/\s+/g, " ").trim();
  const excerpt = normalized.length > 60 ? `${normalized.slice(0, 59)}…` : normalized;
  return `${workflow} — “${excerpt}”`;
}

function displayOutcome(value) {
  return value === "not_applicable" ? "no decision" : String(value).replaceAll("_", " ");
}

function findingEvidence(finding) {
  const metadata = finding.metadata || {};
  if (finding.type === "ambiguity" && metadata.interpretationA && metadata.interpretationB) {
    return [
      `${metadata.interpretationA} → ${displayOutcome(metadata.interpretationAOutcome || metadata.interpretationADecision)}`,
      `${metadata.interpretationB} → ${displayOutcome(metadata.interpretationBOutcome || metadata.interpretationBDecision)}`,
    ];
  }
  if (finding.type === "contradiction") {
    return (finding.ruleIds || [])
      .filter((ruleId) => metadata[ruleId])
      .map((ruleId) => `${ruleId} → ${displayOutcome(metadata[ruleId])}`);
  }
  if (finding.type === "over_strict") {
    const ruleId = finding.ruleIds?.[0];
    if (ruleId && metadata.ruleAction && metadata.expectedOutcome) {
      return [`${ruleId} → ${metadata.ruleAction} · expected → ${metadata.expectedOutcome}`];
    }
    return ["Blocking condition overlaps the extracted safe condition."];
  }
  return [];
}

function findingCard(finding, run) {
  const card = document.createElement("article");
  card.className = "finding-card";
  const top = document.createElement("div");
  top.className = "finding-topline";
  const type = document.createElement("span");
  type.className = "finding-type";
  type.textContent = finding.type.replace("_", " ");
  const severity = document.createElement("span");
  severity.className = "severity";
  severity.textContent = finding.severity;
  const separator = document.createElement("span");
  separator.className = "finding-separator";
  separator.textContent = "·";
  separator.setAttribute("aria-hidden", "true");
  top.append(type, separator, severity);
  const origin = document.createElement("p");
  origin.className = "finding-origin";
  origin.textContent = findingOrigin(finding, run);
  origin.title = finding.metadata?.exampleContent || origin.textContent;
  const headline = document.createElement("p");
  headline.className = "finding-headline";
  headline.textContent = findingHeadline(finding);
  const evidence = document.createElement("div");
  evidence.className = "finding-evidence";
  for (const line of findingEvidence(finding)) {
    const item = document.createElement("span");
    item.textContent = line;
    evidence.append(item);
  }
  const tags = document.createElement("div");
  tags.className = "rule-tags";
  for (const ruleId of finding.ruleIds || []) {
    const tag = document.createElement("span");
    tag.className = "rule-tag";
    tag.textContent = ruleId;
    tags.append(tag);
  }
  card.append(top, origin, headline);
  if (evidence.childElementCount) card.append(evidence);
  const proposedFix = proposedFixSection(finding, run);
  if (proposedFix) card.append(proposedFix);
  const footer = document.createElement("div");
  footer.className = "finding-footer";
  footer.append(tags);
  const generate = generateFixAction(finding, run);
  if (generate) footer.append(generate);
  card.append(footer);
  bindFindingCard(card, finding.id, () => {
    pinFindings([finding.id]);
    const source = [...document.querySelectorAll(".source-reference")]
      .find((node) => JSON.parse(node.dataset.findingIds).includes(finding.id));
    source?.scrollIntoView({behavior: "smooth", block: "center"});
  });
  return card;
}

async function generateProposedFix(finding, run) {
  const study = state.activeStudy;
  if (!study || !run) return;
  const key = proposalKey(study, finding.id);
  const existing = state.proposalControllers.get(key);
  if (existing) {
    existing.abort();
    return;
  }
  const thinkingBudget = studyThinkingBudget(study);
  const controller = new AbortController();
  state.proposalControllers.set(key, controller);
  renderFindings(run);
  setNotice("");
  try {
    const proposal = await api("/api/v1/proposed-fixes", {
      method: "POST",
      body: JSON.stringify({
        specification: {name: study.specificationName, text: study.specificationText},
        workflow: run.workflow || "scan",
        findingId: finding.id,
        contentHash: run.contentHash,
        provider: study.settings.provider,
        model: study.settings.model,
        thinkingBudget,
        ...(study.taxonomy ? {taxonomy: study.taxonomy} : {}),
        ...(run.workflow === "diagnose" && study.incidents ? {violations: study.incidents} : {}),
      }),
      signal: controller.signal,
    });
    if (state.activeStudy?.id !== study.id || controller.signal.aborted || matchingCachedRun(study) !== run) return;
    if (proposal.status === "validated") {
      run.proposedFixes = {...(run.proposedFixes || {}), [finding.id]: proposal};
      if (!study.bundled) await saveStudy(study);
      setNotice("Proposed fix checks passed.", "success");
    } else {
      state.proposalResults.set(key, proposal);
    }
  } catch (error) {
    if (error.name === "AbortError") setNotice("Proposed-fix generation cancelled.");
    else setNotice(error.message);
  } finally {
    if (state.proposalControllers.get(key) === controller) {
      state.proposalControllers.delete(key);
      if (state.activeStudy?.id === study.id && matchingCachedRun(study) === run) renderFindings(run);
    }
  }
}

function bindFindingPreview(node, findingIds) {
  node.addEventListener("mouseenter", () => previewFindings(findingIds));
  node.addEventListener("mouseleave", clearPreview);
  node.addEventListener("focus", () => previewFindings(findingIds));
  node.addEventListener("blur", clearPreview);
}

function bindFindingCard(card, findingId, activate) {
  const findingIds = Array.isArray(findingId) ? findingId : [findingId];
  card.tabIndex = 0;
  card.dataset.findingId = findingIds[0];
  card.dataset.findingIds = JSON.stringify(findingIds);
  bindFindingPreview(card, findingIds);
  card.addEventListener("click", (event) => {
    if (!event.target.closest?.("button, a, input, summary, details")) activate();
  });
  card.addEventListener("keydown", (event) => {
    if (event.target === card && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      activate();
    }
  });
}

function previewFindings(ids) {
  state.hoveredFindingIds = ids;
  applyHighlights();
}

function clearPreview() {
  state.hoveredFindingIds = [];
  applyHighlights();
}

function pinFindings(ids, sourceRange = null) {
  state.pinnedFindingIds = ids;
  state.pinnedSourceRange = sourceRange;
  applyHighlights();
}

function applyHighlights() {
  const showingPreview = state.hoveredFindingIds.length > 0;
  const active = new Set(showingPreview ? state.hoveredFindingIds : state.pinnedFindingIds);
  const range = showingPreview ? null : state.pinnedSourceRange;
  document.querySelectorAll(".source-reference").forEach((node) => {
    const ids = JSON.parse(node.dataset.findingIds);
    const inRange = !range || (
      range.documentId === elements.sourceView.dataset.documentId
      && Number(node.dataset.sourceStart) < range.end
      && Number(node.dataset.sourceEnd) > range.start
    );
    node.classList.toggle("active", inRange && ids.some((id) => active.has(id)));
  });
  document.querySelectorAll(".finding-card").forEach((node) => {
    const ids = node.dataset.findingIds ? JSON.parse(node.dataset.findingIds) : [node.dataset.findingId];
    node.classList.toggle("active", ids.some(id => active.has(id)));
  });
}
