"use strict";

function studyColor(study) {
  const index = [...study.id].reduce((total, character) => total + character.codePointAt(0), 0);
  return STUDY_COLORS[index % STUDY_COLORS.length];
}

function studyActionIcon(name) {
  const namespace = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(namespace, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  const paths = name === "edit"
    ? ["M4 20h4L19 9a2.1 2.1 0 0 0 0-3l-1-1a2.1 2.1 0 0 0-3 0L5 16l-1 4Z", "m14 5 5 5"]
    : ["M4 7h16", "M9 7V4h6v3", "m7 7 1 13h8l1-13", "M10 11v5", "M14 11v5"];
  for (const data of paths) {
    const path = document.createElementNS(namespace, "path");
    path.setAttribute("d", data);
    svg.append(path);
  }
  return svg;
}

function studyAction(label, icon, action, className = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `study-action ${className}`.trim();
  button.setAttribute("aria-label", label);
  button.append(studyActionIcon(icon));
  button.addEventListener("click", action);
  return button;
}

function studyRow(study) {
  const row = document.createElement("div");
  row.className = "study-row";
  row.dataset.studyId = study.id;
  row.setAttribute("role", "listitem");
  row.classList.toggle("active", study.id === state.activeStudy?.id);
  row.style.setProperty("--study-color", studyColor(study));

  const dot = document.createElement("span");
  dot.className = "study-dot";
  dot.setAttribute("aria-hidden", "true");

  if (state.editingStudyId === study.id) {
    const editor = document.createElement("div");
    editor.className = "study-editor";
    const input = document.createElement("input");
    input.className = "study-title-input";
    input.type = "text";
    input.value = study.title;
    input.setAttribute("aria-label", `Rename ${study.title}`);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        finishRenamingStudy(study, input.value);
      } else if (event.key === "Escape") {
        event.preventDefault();
        state.editingStudyId = null;
        renderStudyLists();
      }
    });
    input.addEventListener("blur", () => {
      window.setTimeout(() => finishRenamingStudy(study, input.value), 0);
    });
    editor.append(dot, input);
    row.append(editor);
  } else {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "study-select";
    button.setAttribute("aria-pressed", String(study.id === state.activeStudy?.id));
    const title = document.createElement("span");
    title.className = "study-title";
    title.textContent = study.title;
    button.append(dot, title);
    if (isAuditStudy(study)) button.append(auditElement("span", "adherence", "study-kind-badge"));
    button.addEventListener("click", () => selectStudy(study));
    row.append(button);
  }

  if (study.id === state.activeStudy?.id && state.editingStudyId !== study.id) {
    const actions = document.createElement("div");
    actions.className = "study-actions";
    actions.append(
      studyAction(`Rename ${study.title}`, "edit", () => startRenamingStudy(study)),
      studyAction(`Delete ${study.title}`, "delete", () => deleteStudy(study), "delete"),
    );
    row.append(actions);
  }
  return row;
}

function renderStudyLists() {
  elements.sampleList.replaceChildren(...state.samples.map(studyRow));
  elements.importList.replaceChildren(...state.imports.map(studyRow));
  elements.importsEmpty.hidden = state.imports.length > 0;
  elements.restoreSamplesButton.hidden = state.sampleOverrides.size === 0;
  elements.clearButton.hidden = state.imports.length === 0;
  elements.studyCount.textContent = String(state.samples.length + state.imports.length);
}

function normalizeSample(sample) {
  return {
    id: `sample:${sample.id}`,
    title: sample.title,
    specificationName: sample.filename,
    specificationText: sample.text,
    specificationKind: sample.id === "regexes" ? "regex" : "natural_language",
    document: null,
    taxonomy: null,
    settings: defaultStudySettings(),
    incidents: sample.incidentsText
      ? {name: sample.incidentsName, text: sample.incidentsText}
      : null,
    cachedRun: null,
    bundled: true,
  };
}

async function ensureValidated(study) {
  if (isAuditStudy(study)) return validateAuditStudy(study);
  if (study.document) return study;
  const validation = await api("/api/v1/validate", {
    method: "POST",
    body: JSON.stringify({specification: {name: study.specificationName, text: study.specificationText}}),
  });
  study.specificationKind = validation.specificationKind;
  study.document = validation.document;
  return study;
}

async function selectStudy(study) {
  const selection = Symbol("selection");
  state.selection = selection;
  state.auditReplacement = null;
  try {
    state.scanController?.abort();
    state.scanController = null;
    elements.analyzeButton.textContent = runActionLabel(study);
    setNotice("");
    const validated = await ensureValidated(study);
    if (state.selection !== selection) return;
    state.activeStudy = validated;
    state.hoveredFindingIds = [];
    state.pinnedFindingIds = [];
    state.pinnedSourceRange = null;
    renderStudyLists();
    renderActiveStudy();
    closeDrawers(false);
  } catch (error) {
    setNotice(error.message);
  }
}

function renderEmptyWorkspace() {
  configureAuditWorkspace(false);
  state.selection = null;
  elements.scanMode.classList.toggle("active", true);
  elements.scanMode.setAttribute("aria-pressed", "true");
  elements.diagnoseMode.classList.remove("active");
  elements.diagnoseMode.setAttribute("aria-pressed", "false");
  elements.analysisEmpty.textContent = "Select a specification or choose Adherence.";
  elements.studyKind.textContent = "No specification loaded";
  elements.studyTitle.textContent = "Select or import a specification";
  elements.sourceName.textContent = "";
  elements.activeFileControl.hidden = true;
  elements.copyStatus.textContent = "";
  elements.analysisControls.hidden = true;
  elements.workflowToggle.hidden = false;
  elements.scanMode.disabled = false;
  elements.diagnoseMode.disabled = true;
  elements.analysisEmpty.hidden = false;
  elements.providerDisclosure.hidden = true;
  elements.sourceView.replaceChildren();
  const placeholder = document.createElement("span");
  placeholder.className = "source-placeholder";
  placeholder.textContent = "Select a specification, then scan or diagnose it.";
  elements.sourceView.append(placeholder);
  renderFindings(null);
}

function renderActiveStudy() {
  const study = state.activeStudy;
  if (!study) {
    renderEmptyWorkspace();
    return;
  }
  if (isAuditStudy(study)) { renderAuditWorkspace(study); return; }
  configureAuditWorkspace(false);
  elements.studyKind.textContent = study.specificationKind.replace("_", " ");
  elements.studyTitle.textContent = study.title;
  elements.sourceName.textContent = study.specificationName;
  elements.activeFilename.textContent = study.specificationName;
  elements.activeFileControl.hidden = false;
  renderControls(study);
  const run = matchingCachedRun(study);
  renderSource(study.document, run?.findings || []);
  renderFindings(run);
}

function startRenamingStudy(study) {
  state.editingStudyId = study.id;
  renderStudyLists();
  window.requestAnimationFrame(() => {
    const row = [...document.querySelectorAll(".study-row")]
      .find((candidate) => candidate.dataset.studyId === study.id);
    const input = row?.querySelector(".study-title-input");
    input?.focus();
    input?.select();
  });
}

async function finishRenamingStudy(study, requestedTitle) {
  if (state.editingStudyId !== study.id) return;
  state.editingStudyId = null;
  const title = requestedTitle.trim();
  if (!title || title === study.title) {
    renderStudyLists();
    return;
  }
  study.title = title;
  study.updatedAt = new Date().toISOString();
  renderStudyLists();
  renderActiveStudy();
  if (study.bundled) {
    const override = {
      id: `sample-override:${study.id}`,
      recordType: "sample_override",
      sampleId: study.id,
      title,
      deleted: false,
      updatedAt: study.updatedAt,
    };
    state.sampleOverrides.set(study.id, override);
    await saveRecord(override);
  } else {
    await saveStudy(study);
  }
}

async function deleteStudy(study) {
  const message = study.bundled
    ? `Remove “${study.title}” from this browser? You can restore bundled examples later.`
    : `Delete “${study.title}” from this browser?`;
  if (!window.confirm(message)) return;
  if (study.bundled) {
    const override = {
      id: `sample-override:${study.id}`,
      recordType: "sample_override",
      sampleId: study.id,
      title: study.title,
      deleted: true,
      updatedAt: new Date().toISOString(),
    };
    state.sampleOverrides.set(study.id, override);
    state.samples = state.samples.filter((item) => item.id !== study.id);
    await saveRecord(override);
  } else {
    await deleteStoredStudy(study.id);
    state.imports = state.imports.filter((item) => item.id !== study.id);
  }
  if (state.activeStudy?.id === study.id) state.activeStudy = null;
  renderStudyLists();
  if (state.activeStudy) renderActiveStudy();
  else renderEmptyWorkspace();
}

async function clearImports() {
  if (!state.imports.length || !window.confirm("Delete every imported study from this browser?")) return;
  await Promise.all(state.imports.map((study) => deleteStoredStudy(study.id)));
  state.imports = [];
  if (!state.activeStudy?.bundled) state.activeStudy = null;
  renderStudyLists();
  if (state.activeStudy) await selectStudy(state.activeStudy);
  else renderEmptyWorkspace();
}

async function restoreSamples() {
  const selectedId = state.activeStudy?.bundled ? state.activeStudy.id : null;
  await Promise.all(
    [...state.sampleOverrides.values()].map((override) => deleteStoredStudy(override.id)),
  );
  state.sampleOverrides.clear();
  state.samples = state.sampleDefinitions.map(normalizeSample);
  const restoredStudy = state.samples.find((study) => study.id === selectedId);
  renderStudyLists();
  if (restoredStudy) await selectStudy(restoredStudy);
  else if (!state.activeStudy) renderEmptyWorkspace();
  setNotice("Bundled examples restored.", "success");
}

async function copyActiveSpecification() {
  const study = state.activeStudy;
  if (!study) return;
  try {
    await copyText(study.specificationText);
    elements.copyStatus.textContent = `${study.specificationName} copied.`;
    elements.activeFileControl.title = "Copied";
    window.setTimeout(() => {
      elements.copyStatus.textContent = "";
      elements.activeFileControl.title = "Copy specification";
    }, 1800);
  } catch (error) {
    setNotice(error.message);
  }
}
