"use strict";

function analysisRequest(study) {
  const workflow = currentWorkflow(study);
  if (workflow === "audit_judge") {
    const contentHash = study.validation?.contentHash;
    if (typeof contentHash !== "string" || !contentHash) {
      throw new Error("Validate this case before checking adherence.");
    }
    return {path: "/api/v1/judge-audits", payload: {
      case: {name: study.auditCaseName, text: study.auditCaseText},
      contentHash,
    }};
  }
  if (workflow === "diagnose" && !study.incidents) {
    throw new Error("Attach a violations JSON file, then diagnose.");
  }
  const specification = {name: study.specificationName, text: study.specificationText};
  if (workflow === "diagnose") return {path: "/api/v1/diagnoses", payload: {specification, violations: study.incidents}};
  const thinkingBudget = Number.parseInt(elements.thinkingBudgetInput.value, 10);
  if (!Number.isInteger(thinkingBudget) || thinkingBudget < 0) {
    throw new Error("Volume must be a non-negative integer.");
  }
  return {path: "/api/v1/scans", payload: {
    specification, provider: study.settings.provider, model: study.settings.model, thinkingBudget,
    ...(study.taxonomy ? {taxonomy: study.taxonomy} : {}),
  }};
}

function analysisInputKey(study) {
  const workflow = currentWorkflow(study);
  if (workflow === "audit_judge") {
    return JSON.stringify([study.auditCaseText, study.auditCaseName, study.validation?.contentHash]);
  }
  const specification = [workflow, study.specificationText, study.specificationName];
  if (workflow === "diagnose") return JSON.stringify([...specification, study.incidents]);
  return JSON.stringify([...specification, study.settings.provider, study.settings.model,
    study.taxonomy, elements.thinkingBudgetInput.value]);
}

async function analyzeActiveStudy() {
  const study = state.activeStudy;
  if (!study) return;
  if (state.scanController) {
    state.scanController.abort();
    return;
  }
  let request;
  try {
    request = analysisRequest(study);
  } catch (error) {
    setNotice(error.message);
    return;
  }
  const workflow = currentWorkflow(study);
  const verb = {scan: "Scan", diagnose: "Diagnosis", audit_judge: "Adherence check"}[workflow];
  if (workflow === "scan") study.settings.thinkingBudget = request.payload.thinkingBudget;
  const inputKey = analysisInputKey(study);
  const controller = new AbortController();
  state.scanController = controller;
  if (!isAuditStudy(study)) invalidateProposals(study);
  const ownsRun = () => state.activeStudy === study && state.scanController === controller
    && analysisInputKey(study) === inputKey;
  const current = () => ownsRun() && !controller.signal.aborted;
  elements.analyzeButton.textContent = "Cancel";
  setNotice("");
  try {
    const run = await api(request.path, {
      method: "POST", body: JSON.stringify(request.payload), signal: controller.signal,
    });
    if (!current()) return;
    if (workflow === "audit_judge" && run.contentHash !== request.payload.contentHash) {
      throw new Error("Stale adherence response discarded.");
    }
    study.cachedRun = run;
    study.updatedAt = new Date().toISOString();
    if (!study.bundled) await saveStudy(study);
    if (!current()) return;
    renderActiveStudy();
    const outcome = workflow === "audit_judge"
      ? `${run.summary.status.replaceAll("_", " ")}. Review each dimension.`
      : `${findingCountText(run.findings.length)}.`;
    const persistence = state.storageAvailable ? "" : " Session only: local persistence is unavailable.";
    setNotice(`${verb} complete: ${outcome}${persistence}`, "success");
  } catch (error) {
    if (!ownsRun()) return;
    if (error.name === "AbortError") {
      const detail = workflow === "audit_judge" ? " The local solver may finish its bounded checks." : "";
      setNotice(`${verb} cancelled.${detail}`);
    } else {
      setNotice(error.message);
    }
  } finally {
    if (state.scanController === controller) {
      state.scanController = null;
      if (state.activeStudy === study) elements.analyzeButton.textContent = runActionLabel(study);
    }
  }
}

function closeDrawers(restoreFocus = true) {
  elements.specificationsPanel.classList.remove("open");
  elements.analysisPanel.classList.remove("open");
  elements.specificationsToggle.setAttribute("aria-expanded", "false");
  elements.analysisToggle.setAttribute("aria-expanded", "false");
  elements.drawerOverlay.hidden = true;
  const trigger = state.drawerTrigger;
  state.openDrawer = null;
  state.drawerTrigger = null;
  if (restoreFocus) trigger?.focus();
}

function openDrawer(name, trigger) {
  closeDrawers(false);
  const panel = name === "specifications" ? elements.specificationsPanel : elements.analysisPanel;
  const toggle = name === "specifications" ? elements.specificationsToggle : elements.analysisToggle;
  panel.classList.add("open");
  toggle.setAttribute("aria-expanded", "true");
  elements.drawerOverlay.hidden = false;
  state.openDrawer = name;
  state.drawerTrigger = trigger;
  panel.querySelector(".drawer-close")?.focus();
}

function trapDrawerFocus(event) {
  if (event.key !== "Tab" || !state.openDrawer) return;
  const panel = state.openDrawer === "specifications"
    ? elements.specificationsPanel
    : elements.analysisPanel;
  const focusable = [...panel.querySelectorAll(
    "button:not([disabled]):not([hidden]), input:not([disabled]):not([hidden]), select:not([disabled]):not([hidden]), [tabindex]:not([tabindex='-1'])",
  )].filter((node) => node.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function bindFileInput(button, input, handleFile) {
  button.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    const file = input.files[0];
    if (file) handleFile(file);
  });
}

function bindEvents() {
  bindAuditEvents();
  bindFileInput(elements.importButton, elements.fileInput, importSpecification);
  bindFileInput(elements.taxonomyButton, elements.taxonomyInput, attachTaxonomy);
  bindFileInput(elements.incidentsButton, elements.incidentsInput, attachIncidents);
  elements.taxonomyToggle.addEventListener("click", toggleTaxonomy);
  elements.scanMode.addEventListener("click", () => setWorkflow("scan"));
  elements.diagnoseMode.addEventListener("click", () => setWorkflow("diagnose"));
  elements.analyzeButton.addEventListener("click", analyzeActiveStudy);
  elements.clearButton.addEventListener("click", clearImports);
  elements.restoreSamplesButton.addEventListener("click", restoreSamples);
  elements.activeFileControl.addEventListener("click", copyActiveSpecification);
  elements.specificationsToggle.addEventListener("click", (event) => {
    openDrawer("specifications", event.currentTarget);
  });
  elements.analysisToggle.addEventListener("click", (event) => {
    openDrawer("analysis", event.currentTarget);
  });
  elements.specificationsClose.addEventListener("click", () => closeDrawers());
  elements.analysisClose.addEventListener("click", () => closeDrawers());
  elements.drawerOverlay.addEventListener("click", () => closeDrawers());
  elements.sourceTab.addEventListener("click", () => setComparisonTab("source"));
  elements.findingsTab.addEventListener("click", () => setComparisonTab("findings"));
  elements.sourceTab.addEventListener("keydown", handleComparisonTabKeydown);
  elements.findingsTab.addEventListener("keydown", handleComparisonTabKeydown);
  elements.providerPickerButton.addEventListener("click", providerPicker.toggle);
  elements.modelPickerButton.addEventListener("click", modelPicker.toggle);
  elements.providerMenu.addEventListener("keydown", handlePickerMenuKeydown);
  elements.modelMenu.addEventListener("keydown", handlePickerMenuKeydown);
  elements.thinkingBudgetInput.addEventListener("input", updateThinkingBudget);
  document.addEventListener("keydown", (event) => {
    trapDrawerFocus(event);
    if (event.key !== "Escape") return;
    if (!elements.providerMenu.hidden) providerPicker.close({restoreFocus: true});
    else if (!elements.modelMenu.hidden) modelPicker.close({restoreFocus: true});
    else if (state.openDrawer) closeDrawers();
    else if (state.proposalControllers.size) {
      for (const controller of state.proposalControllers.values()) controller.abort();
    }
    else pinFindings([]);
  });
  document.addEventListener("click", (event) => {
    if (!elements.providerControl.contains(event.target)) providerPicker.close();
    if (!elements.modelControl.contains(event.target)) modelPicker.close();
  });
  window.addEventListener("resize", () => {
    if (window.innerWidth >= 1200 && state.openDrawer) closeDrawers(false);
  });
}

async function initialize() {
  bindElements();
  bindEvents();
  try {
    const [capabilities, samplesPayload] = await Promise.all([api("/api/v1/capabilities"), api("/api/v1/samples")]);
    state.capabilities = capabilities;
    elements.appVersion.textContent = `v${capabilities.version}`;
    state.sampleDefinitions = samplesPayload.samples;
    state.samples = state.sampleDefinitions.map(normalizeSample);
    try {
      state.database = await openDatabase();
      await loadImports();
      elements.storageStatus.textContent = "Studies stay in this browser";
    } catch (error) {
      state.storageAvailable = false;
      elements.storageStatus.textContent = "Session storage only";
      setNotice(error.message);
    }
    renderStudyLists();
    renderEmptyWorkspace();
  } catch (error) {
    setNotice(`Studio initialization failed: ${error.message}`);
  }
}

document.addEventListener("DOMContentLoaded", initialize);
