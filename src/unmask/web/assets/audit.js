"use strict";

function isAuditStudy(study) {
  return study?.recordType === "judge_audit";
}

function auditElement(tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  node.className = className;
  return node;
}

function configureAuditWorkspace(enabled) {
  element("audit-source-controls").hidden = !enabled;
  element("audit-actions").hidden = !enabled;
  element("audit-import-panel").hidden = true;
  element("audit-annotations").hidden = true;
  elements.sourceView.hidden = false;
  elements.sourceView.classList.toggle("audit-source", enabled);
  elements.sourceTab.textContent = enabled ? "Sources / annotations" : "Specification";
  element("findings-heading").replaceChildren(auditElement("span", enabled ? "Audit results" : "specification bugs"), auditElement("small", enabled ? "(replay)" : "(structure)"));
  element("source-heading").replaceChildren(auditElement("span", enabled ? "Case sources" : "specification"), auditElement("small", "(source)"));
  elements.findingsTab.firstChild.textContent = enabled ? "Results " : "Bugs ";
  element("audit-mode").classList.toggle("active", enabled);
  element("audit-mode").setAttribute("aria-pressed", String(enabled));
  element("audit-verdict").hidden = !enabled;
}

function startAuditImport(replace = false) {
  state.scanController?.abort();
  state.auditReplacement = replace && isAuditStudy(state.activeStudy) ? state.activeStudy : null;
  if (!state.auditReplacement) {
    state.activeStudy = null;
    renderEmptyWorkspace();
  }
  state.selection = Symbol("audit import");
  configureAuditWorkspace(true);
  element("audit-source-controls").hidden = true;
  element("audit-actions").hidden = true;
  element("audit-verdict").hidden = true;
  elements.sourceView.hidden = true;
  elements.analysisControls.hidden = true;
  elements.analysisEmpty.hidden = false;
  elements.analysisEmpty.textContent = "Import annotated JSON. Audits run locally without a model provider.";
  elements.workflowToggle.hidden = false;
  elements.scanMode.classList.remove("active");
  elements.scanMode.setAttribute("aria-pressed", "false");
  elements.diagnoseMode.classList.remove("active");
  elements.diagnoseMode.setAttribute("aria-pressed", "false");
  elements.studyTitle.textContent = replace ? "Replace audit case" : "Audit a judge evaluation";
  elements.activeFileControl.hidden = true;
  element("audit-import-panel").hidden = false;
  element("audit-paste").value = "";
  element("audit-import-cancel").hidden = !state.auditReplacement;
  element("audit-import-cancel").disabled = false;
  renderFindingMessage("Review supplied annotations, then run the audit. Raw prose is not automatically translated.");
  elements.summary.hidden = true;
  setNotice("");
  renderStudyLists();
}

async function validateAuditStudy(study) {
  const text = study.auditCaseText;
  const validation = await api("/api/v1/judge-audits/validate", {
    method: "POST", body: JSON.stringify({case: {name: study.auditCaseName, text}}),
  });
  if (text !== study.auditCaseText) throw new Error("Case changed during validation. Select it again.");
  study.validation = validation;
  if (!UnmaskAudit.cached(study, state.capabilities.judgeAudits.auditorVersion)) study.cachedRun = null;
  return study;
}

async function importAuditText(text, name = "audit-case.json") {
  const selection = Symbol("audit validation");
  state.selection = selection;
  const replacement = state.auditReplacement;
  const input = UnmaskAudit.input(text);
  const validation = await api("/api/v1/judge-audits/validate", {
    method: "POST", body: JSON.stringify({case: {name, text: input.text}}),
  });
  if (state.selection !== selection) return null;
  const now = new Date().toISOString();
  const study = replacement
    ? UnmaskAudit.replace(replacement, input.text, name, validation)
    : {id: `study:${crypto.randomUUID()}`, recordType: "judge_audit", recordVersion: 1,
      title: uniqueTitle(validation.name), auditCaseName: name, auditCaseText: input.text,
      validation, settings: {workflow: "audit_judge"}, cachedRun: null, bundled: false,
      activeDocument: "protocol", auditView: "sources", createdAt: now, updatedAt: now};
  state.scanController?.abort();
  state.scanController = null;
  const cancel = element("audit-import-cancel");
  // Once persistence starts, cancellation cannot promise to retain the old case.
  cancel.disabled = true;
  try {
    await saveStudy(study);
  } finally {
    if (state.selection === selection) cancel.disabled = false;
  }
  if (replacement) state.imports = state.imports.map((item) => item.id === replacement.id ? study : item);
  else state.imports.unshift(study);
  if (state.selection !== selection) return null;
  state.auditReplacement = null;
  state.activeStudy = study;
  state.pinnedFindingIds = [];
  state.pinnedSourceRange = null;
  state.hoveredFindingIds = [];
  renderStudyLists();
  renderActiveStudy();
  setNotice(input.historical ? "Report case imported. Run a fresh audit; imported results are not treated as verified." : "Case loaded. Review sources and annotations, then run the local audit.", "success");
  closeDrawers(false);
  return study;
}

async function auditAction(action) {
  try { await action(); } catch (error) { setNotice(error.message); }
}

function auditReferences(study) {
  return study.validation.case.findings.map((finding, index) => ({
    id: `annotation:${index}`,
    sourceReferences: finding.evidence.map((evidence) => ({
      documentId: evidence.source, start: evidence.start, end: evidence.end,
    })),
  }));
}

function showAuditEvidence(study, reference, identifier) {
  study.auditView = "sources";
  study.activeDocument = reference.documentId;
  renderAuditSource(study);
  const source = study.validation.documents.find((document) => document.id === reference.documentId).rawText;
  state.hoveredFindingIds = [];
  pinFindings([identifier], {
    documentId: reference.documentId,
    start: codePointToCodeUnit(source, reference.start),
    end: codePointToCodeUnit(source, reference.end),
  });
  setComparisonTab("source");
  elements.sourceView.querySelector(".source-reference.active")?.scrollIntoView({block: "center"});
}

function auditEvidenceButtons(study, references, identifier) {
  const group = auditElement("div", "", "audit-evidence-links");
  for (const reference of references) {
    const button = auditElement("button", `${reference.documentId} · ${reference.start}–${reference.end}`, "text-button");
    button.type = "button";
    button.addEventListener("click", () => showAuditEvidence(study, reference, identifier));
    group.append(button);
  }
  return group;
}

function renderAuditAnnotations(study) {
  const data = study.validation.case;
  const panel = element("audit-annotations");
  panel.replaceChildren();
  panel.append(auditElement("p", `Supplied by ${data.extraction.provider}. Translation: ${data.extraction.translation_status}. Span validity does not prove semantic interpretation.`, "audit-note"));
  if (data.extraction.notes) panel.append(auditElement("p", data.extraction.notes, "audit-note"));
  const expressions = auditElement("details", "");
  expressions.append(auditElement("summary", "Decision, constraints, obligations and grounding pairs"));
  expressions.append(auditElement("pre", JSON.stringify({decision: data.protocol.decision, constraints: data.protocol.constraints,
    requirements: data.protocol.requirements, grounding_pairs: data.grounding_pairs || []}, null, 2), "audit-json"));
  panel.append(expressions);
  data.protocol.atoms.forEach((atom) => {
    const card = auditElement("article", "", "audit-annotation");
    card.append(auditElement("h3", `${atom.id} · ${atom.role}`), auditElement("p", atom.description));
    const findings = data.findings.map((finding, index) => ({finding, index})).filter(({finding}) => finding.atom === atom.id);
    if (!findings.length) card.append(auditElement("p", "UNKNOWN · no supplied observation"));
    for (const {finding, index} of findings) {
      card.append(auditElement("p", `${finding.value} · ${finding.explanation}`));
      card.append(auditEvidenceButtons(study, finding.evidence.map((evidence) => ({...evidence, documentId: evidence.source})), `annotation:${index}`));
    }
    panel.append(card);
  });
}

function renderAuditSource(study) {
  const documents = study.validation.documents;
  const selected = documents.find((item) => item.id === study.activeDocument) || documents[0];
  study.activeDocument = selected.id;
  const picker = element("audit-document");
  picker.replaceChildren(...documents.map((item) => {
    const option = auditElement("option", item.name);
    option.value = item.id;
    return option;
  }));
  picker.value = selected.id;
  const annotations = study.auditView === "annotations";
  elements.sourceView.hidden = annotations;
  picker.hidden = annotations;
  element("audit-annotations").hidden = !annotations;
  element("audit-sources-button").setAttribute("aria-pressed", String(!annotations));
  element("audit-annotations-button").setAttribute("aria-pressed", String(annotations));
  elements.sourceName.textContent = annotations ? "Supplied annotations" : selected.name;
  if (annotations) renderAuditAnnotations(study);
  else {
    const run = UnmaskAudit.cached(study, state.capabilities.judgeAudits.auditorVersion);
    renderSource(selected, [...auditReferences(study), ...(run?.checks || [])]);
    applyHighlights();
  }
}

function auditProofDetails(proof) {
  const details = auditElement("details", "", "audit-proof");
  details.append(auditElement("summary", "Solver queries and possible assignments"));
  details.append(auditElement("p", "Countermodels are possible worlds, not observed facts. Completed unknown values are not new evidence."));
  details.append(auditElement("pre", JSON.stringify(proof, null, 2), "audit-json"));
  return details;
}

function renderAuditResults(study) {
  const run = UnmaskAudit.cached(study, state.capabilities.judgeAudits.auditorVersion);
  elements.summary.hidden = !run;
  elements.findingCount.textContent = String(run?.checks.length || 0);
  elements.findingCount.setAttribute("aria-label", `${run?.checks.length || 0} audit checks`);
  elements.mobileFindingCount.textContent = String(run?.checks.length || 0);
  element("audit-export-report").disabled = !run;
  if (!run) {
    renderFindingMessage("Run the local audit to check the supplied annotations. No live extraction is performed.");
    return;
  }
  elements.summary.textContent = `${run.summary.status.replaceAll("_", " ")} · protocol adherence ${run.summary.overall.toLowerCase()}`;
  elements.findingList.replaceChildren();
  const dimensions = auditElement("dl", "", "audit-dimensions");
  for (const [key, label] of [["coverage", "Observable coverage"], ["procedure", "Procedural adherence"], ["grounding", "Context grounding"], ["consistency", "Rationale / verdict compatibility"], ["entailment", "Verdict entailment"]]) {
    dimensions.append(auditElement("dt", label), auditElement("dd", run.summary[key].replaceAll("_", " ")));
  }
  elements.findingList.append(dimensions);
  if (run.audit.gold_verdict) elements.findingList.append(auditElement("p", `Gold comparison (separate): observed ${run.audit.observed_verdict}; gold ${run.audit.gold_verdict}; ${run.audit.verdict_correct ? "matches" : "differs"}.`, "audit-note"));
  elements.findingList.append(auditElement("p", `Unconstrained atoms: ${run.unknownAtoms.join(", ") || "none"}. Compatibility alone does not establish entailment.`, "audit-note"));
  if (run.audit.solver?.unsat_core?.length) elements.findingList.append(auditElement("p", `Conflicting inputs: ${run.audit.solver.unsat_core.join(", ")}. This conflict set need not be minimal.`, "audit-note"));
  for (const check of run.checks) {
    const card = auditElement("article", "", "finding-card audit-check");
    card.dataset.findingId = check.id;
    card.tabIndex = 0;
    card.append(auditElement("p", `${check.dimension} · ${check.status.replaceAll("_", " ")}`, "finding-type"), auditElement("h3", check.summary));
    if (check.dimension === "coverage" && check.status !== "ENTAILED") card.append(auditElement("p", "Not established by observable evidence. This makes no claim about hidden reasoning."));
    card.append(auditEvidenceButtons(study, check.sourceReferences, check.id));
    card.append(auditProofDetails(check.proof));
    bindFindingPreview(card, [check.id]);
    card.addEventListener("click", (event) => { if (event.target === card) pinFindings([check.id]); });
    card.addEventListener("keydown", (event) => {
      if (event.target === card && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        if (check.sourceReferences.length) showAuditEvidence(study, check.sourceReferences[0], check.id);
        else pinFindings([check.id]);
      }
    });
    elements.findingList.append(card);
  }
  for (const limitation of run.limitations) elements.findingList.append(auditElement("p", limitation, "audit-note"));
}

function renderAuditWorkspace(study) {
  configureAuditWorkspace(true);
  elements.studyTitle.textContent = study.title;
  elements.studyKind.textContent = "Judge audit · replay";
  elements.activeFileControl.hidden = true;
  elements.analysisEmpty.hidden = true;
  elements.analysisControls.hidden = false;
  elements.workflowToggle.hidden = false;
  elements.scanMode.classList.remove("active");
  elements.diagnoseMode.classList.remove("active");
  elements.scanMode.setAttribute("aria-pressed", "false");
  elements.diagnoseMode.setAttribute("aria-pressed", "false");
  elements.diagnoseMode.disabled = true;
  for (const control of [elements.providerControl, elements.modelControl, elements.thinkingBudgetControl,
    elements.taxonomyControl, elements.incidentsButton, elements.regexModelNote, elements.workflowNote]) control.hidden = true;
  elements.providerDisclosure.hidden = false;
  elements.providerDisclosure.textContent = "Audits supplied annotations locally. No model provider is called.";
  elements.analyzeButton.textContent = state.scanController ? "Cancel" : "Run audit";
  elements.analyzeButton.disabled = false;
  element("audit-verdict").textContent = `Observed judge verdict: ${study.validation.case.verdict} · read-only replay`;
  renderAuditSource(study);
  renderAuditResults(study);
}

async function runAuditStudy(study) {
  if (state.scanController) { state.scanController.abort(); return; }
  const controller = new AbortController();
  const text = study.auditCaseText;
  state.scanController = controller;
  elements.analyzeButton.textContent = "Cancel";
  setNotice("");
  try {
    const report = await api("/api/v1/judge-audits", {
      method: "POST", signal: controller.signal,
      body: JSON.stringify({case: {name: study.auditCaseName, text}, contentHash: study.validation.contentHash}),
    });
    if (!UnmaskAudit.current(study, state.activeStudy, text, controller)) return;
    if (report.contentHash !== study.validation.contentHash) throw new Error("Stale audit response discarded.");
    study.cachedRun = report;
    study.updatedAt = new Date().toISOString();
    await saveStudy(study);
    if (!UnmaskAudit.current(study, state.activeStudy, text, controller)) return;
    renderAuditWorkspace(study);
    setNotice(`Audit complete: ${report.summary.status.replaceAll("_", " ")}. Review each dimension.`, "success");
  } catch (error) {
    if (state.activeStudy === study) setNotice(error.name === "AbortError" ? "Audit cancelled. The local solver may finish its bounded checks." : error.message);
  } finally {
    if (state.scanController === controller) {
      state.scanController = null;
      if (state.activeStudy === study) elements.analyzeButton.textContent = "Run audit";
    }
  }
}

function exportAudit(report) {
  const study = state.activeStudy;
  if (!isAuditStudy(study)) return;
  const payload = report ? UnmaskAudit.cached(study, state.capabilities.judgeAudits.auditorVersion) : study.validation.case;
  if (!payload) return;
  const blob = new Blob([JSON.stringify(payload, null, 2) + "\n"], {type: "application/json"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = report ? "judge-audit-report.json" : "judge-audit-case.json";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function bindAuditEvents() {
  element("audit-mode").addEventListener("click", () => {
    if (isAuditStudy(state.activeStudy)) renderAuditWorkspace(state.activeStudy);
    else startAuditImport();
  });
  element("audit-import-file").addEventListener("click", () => element("audit-file").click());
  element("audit-file").addEventListener("change", (event) => auditAction(async () => {
    const file = event.target.files[0];
    const selection = state.selection;
    try {
      if (file) {
        const text = await readTextFile(file, /\.json$/i, "Choose an audit JSON file.");
        if (selection === state.selection) await importAuditText(text, file.name);
      }
    }
    finally { event.target.value = ""; }
  }));
  element("audit-load-paste").addEventListener("click", () => auditAction(() => importAuditText(element("audit-paste").value)));
  element("audit-example").addEventListener("click", () => auditAction(async () => {
    const selection = state.selection;
    const example = await api("/api/v1/judge-audits/example");
    if (selection === state.selection) await importAuditText(example.text, example.name);
  }));
  element("audit-new").addEventListener("click", () => startAuditImport());
  element("audit-replace").addEventListener("click", () => startAuditImport(true));
  element("audit-import-cancel").addEventListener("click", () => {
    if (element("audit-import-cancel").disabled) return;
    state.selection = Symbol("cancel audit import");
    state.activeStudy = state.auditReplacement;
    state.auditReplacement = null;
    renderActiveStudy();
  });
  element("audit-document").addEventListener("change", (event) => {
    state.activeStudy.activeDocument = event.target.value;
    renderAuditSource(state.activeStudy);
  });
  for (const [id, view] of [["audit-sources-button", "sources"], ["audit-annotations-button", "annotations"]]) {
    element(id).addEventListener("click", () => {
      state.activeStudy.auditView = view;
      renderAuditSource(state.activeStudy);
    });
  }
  element("audit-export-case").addEventListener("click", () => exportAudit(false));
  element("audit-export-report").addEventListener("click", () => exportAudit(true));
}
