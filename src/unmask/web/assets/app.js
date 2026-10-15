"use strict";

const DATABASE_NAME = "unmask-studio";
const DATABASE_VERSION = 1;
const STUDY_STORE = "studies";

const state = {
  capabilities: null,
  sampleDefinitions: [],
  samples: [],
  sampleOverrides: new Map(),
  imports: [],
  activeStudy: null,
  editingStudyId: null,
  hoveredFindingIds: [],
  pinnedFindingIds: [],
  pinnedSourceRange: null,
  scanController: null,
  proposalControllers: new Map(),
  proposalResults: new Map(),
  database: null,
  storageAvailable: true,
  openDrawer: null,
  drawerTrigger: null,
};

const elements = {};
const DEFAULT_THINKING_BUDGET = 128;
const STUDY_COLORS = [
  "#62bdf0", "#f2bd47", "#49a8e1", "#dc55a5", "#ded6c6", "#c7912c",
  "#7d7e7e", "#55c45c", "#8951e6", "#4bbb7c", "#ed6a64", "#cf9b62",
  "#5388e2", "#4aa78e", "#db7b57", "#4b98a0", "#696dc1",
];
const MODEL_COLORS = {
  "gemini-2.5-flash": "#63b8e7",
  "gemini-2.5-pro": "#8951e6",
  "gemini-2.5-flash-lite": "#4aa78e",
  "gpt-4o": "#ed6a64",
  "gpt-4o-mini": "#f2bd47",
  "gpt-4.1": "#4b98a0",
  "gpt-4.1-mini": "#696dc1",
  "claude-opus-4-5": "#dc55a5",
  "claude-sonnet-4-5": "#55c45c",
  "claude-haiku-4-5": "#cf9b62",
};

function defaultStudySettings() {
  return {
    provider: "gemini",
    model: "",
    workflow: "scan",
    thinkingBudget: DEFAULT_THINKING_BUDGET,
  };
}

function element(id) {
  return document.getElementById(id);
}

function setNotice(message, kind = "error") {
  elements.notice.textContent = message;
  elements.notice.className = `notice ${kind}`;
  elements.notice.hidden = !message;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error?.message || `Request failed (${response.status})`);
  }
  return payload;
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) {
      reject(new Error("IndexedDB is unavailable"));
      return;
    }
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STUDY_STORE)) {
        database.createObjectStore(STUDY_STORE, {keyPath: "id"});
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("Could not open local storage"));
  });
}

function storageRequest(mode, action) {
  return new Promise((resolve, reject) => {
    if (!state.database) {
      resolve(undefined);
      return;
    }
    const transaction = state.database.transaction(STUDY_STORE, mode);
    const store = transaction.objectStore(STUDY_STORE);
    const request = action(store);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("Local storage failed"));
  });
}

async function loadImports() {
  const records = await storageRequest("readonly", (store) => store.getAll());
  const storedRecords = Array.isArray(records)
    ? records.filter(
      (record) => record
        && typeof record.id === "string"
        && typeof record.updatedAt === "string",
    )
    : [];
  state.sampleOverrides = new Map(
    storedRecords
      .filter(
        (record) => record.recordType === "sample_override"
          && typeof record.sampleId === "string"
          && typeof record.title === "string"
          && typeof record.deleted === "boolean",
      )
      .map((record) => [record.sampleId, record]),
  );
  state.samples = state.samples
    .filter((sample) => !state.sampleOverrides.get(sample.id)?.deleted)
    .map((sample) => ({
      ...sample,
      title: state.sampleOverrides.get(sample.id)?.title || sample.title,
    }));
  state.imports = storedRecords
      .filter((study) => study.recordType !== "sample_override" && !study.bundled)
      .map(UnmaskAudit.normalize)
      .filter(Boolean)
      .map((study) => ({
        ...study,
        settings: {
          ...defaultStudySettings(),
          ...(study.settings || {}),
        },
        incidents: study.incidents || null,
      }))
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

async function saveRecord(record) {
  if (!state.storageAvailable) return;
  try {
    await storageRequest("readwrite", (store) => store.put(record));
  } catch (error) {
    state.storageAvailable = false;
    elements.storageStatus.textContent = "Local persistence unavailable";
    setNotice(`The study remains available for this session. ${error.message}`);
  }
}

async function saveStudy(study) {
  await saveRecord(study);
}

async function deleteStoredStudy(id) {
  await storageRequest("readwrite", (store) => store.delete(id));
}

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
    if (isAuditStudy(study)) button.append(auditElement("span", "audit", "study-kind-badge"));
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

function providerById(id) {
  return state.capabilities.providers.find((provider) => provider.id === id);
}

function handlePickerMenuKeydown(event) {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  const options = [...event.currentTarget.querySelectorAll(".picker-option:not(:disabled)")];
  if (!options.length) return;
  event.preventDefault();
  const currentIndex = options.indexOf(document.activeElement);
  let nextIndex;
  if (event.key === "Home") nextIndex = 0;
  else if (event.key === "End") nextIndex = options.length - 1;
  else if (event.key === "ArrowDown") nextIndex = (currentIndex + 1) % options.length;
  else nextIndex = (currentIndex - 1 + options.length) % options.length;
  options[nextIndex].focus();
}

function closeProviderMenu({restoreFocus = false} = {}) {
  if (elements.providerMenu.hidden) return;
  elements.providerMenu.hidden = true;
  elements.providerPickerButton.setAttribute("aria-expanded", "false");
  if (restoreFocus) elements.providerPickerButton.focus();
}

function chooseProvider(provider) {
  const study = state.activeStudy;
  if (!study) return;
  if (study.settings.provider === provider.id) {
    closeProviderMenu({restoreFocus: true});
    return;
  }
  study.settings.provider = provider.id;
  study.settings.model = provider.defaultModel;
  closeProviderMenu({restoreFocus: true});
  refreshAfterScanSettingsChange(study);
  renderControls(study);
}

function providerOption(provider) {
  const option = document.createElement("button");
  option.type = "button";
  option.className = "picker-option";
  option.setAttribute("role", "option");
  option.setAttribute("aria-selected", String(provider.id === state.activeStudy?.settings.provider));
  const name = document.createElement("span");
  name.textContent = provider.id;
  option.append(name);
  if (!provider.configured) {
    const status = document.createElement("small");
    status.textContent = "not configured";
    option.append(status);
  }
  option.addEventListener("click", () => chooseProvider(provider));
  return option;
}

function renderProviderPicker(provider, enabled) {
  elements.providerValue.textContent = provider.id;
  elements.providerPickerButton.disabled = !enabled;
  elements.providerPickerButton.title = enabled
    ? `Choose provider. Current provider: ${provider.id}`
    : provider.id;
  elements.providerMenu.replaceChildren(
    ...state.capabilities.providers.map(providerOption),
  );
  if (!enabled) closeProviderMenu();
}

function toggleProviderMenu() {
  if (elements.providerPickerButton.disabled) return;
  const opening = elements.providerMenu.hidden;
  closeModelMenu();
  elements.providerMenu.hidden = !opening;
  elements.providerPickerButton.setAttribute("aria-expanded", String(opening));
  if (opening) elements.providerMenu.querySelector('[aria-selected="true"]')?.focus();
}

function modelColor(model) {
  return MODEL_COLORS[model] || "#63b8e7";
}

function selectedModel(provider, requestedModel) {
  return provider.models.includes(requestedModel) ? requestedModel : provider.defaultModel;
}

function closeModelMenu({restoreFocus = false} = {}) {
  if (elements.modelMenu.hidden) return;
  elements.modelMenu.hidden = true;
  elements.modelPickerButton.setAttribute("aria-expanded", "false");
  if (restoreFocus) elements.modelPickerButton.focus();
}

function chooseModel(model) {
  const study = state.activeStudy;
  if (!study) return;
  const changed = study.settings.model !== model;
  study.settings.model = model;
  closeModelMenu({restoreFocus: true});
  if (changed) {
    refreshAfterScanSettingsChange(study);
  }
  renderControls(study);
}

function modelOption(model) {
  const option = document.createElement("button");
  option.type = "button";
  option.className = "picker-option model-option";
  option.setAttribute("role", "option");
  option.setAttribute("aria-selected", String(model === state.activeStudy?.settings.model));
  const color = document.createElement("span");
  color.className = "model-color";
  color.style.setProperty("--model-color", modelColor(model));
  color.setAttribute("aria-hidden", "true");
  const name = document.createElement("span");
  name.className = "model-option-name";
  name.textContent = model;
  option.append(color, name);
  option.addEventListener("click", () => chooseModel(model));
  return option;
}

function renderModelPicker(provider, model, enabled) {
  elements.modelValue.textContent = model;
  elements.modelColor.style.setProperty("--model-color", modelColor(model));
  elements.modelPickerButton.disabled = !enabled;
  elements.modelPickerButton.title = enabled ? `Choose model. Current model: ${model}` : model;
  elements.modelMenu.replaceChildren(...provider.models.map(modelOption));
  if (!enabled) closeModelMenu();
}

function toggleModelMenu() {
  if (elements.modelPickerButton.disabled) return;
  const opening = elements.modelMenu.hidden;
  closeProviderMenu();
  elements.modelMenu.hidden = !opening;
  elements.modelPickerButton.setAttribute("aria-expanded", String(opening));
  if (opening) elements.modelMenu.querySelector('[aria-selected="true"]')?.focus();
}

function currentWorkflow(study) {
  if (isAuditStudy(study)) return "audit_judge";
  const workflow = study.settings.workflow || "scan";
  if (!["scan", "diagnose"].includes(workflow)) throw new Error("Unsupported study workflow");
  return workflow;
}

function runActionLabel(study) {
  return {scan: "Scan", diagnose: "Diagnose", audit_judge: "Run audit"}[currentWorkflow(study)];
}

function matchingCachedRun(study) {
  const run = study.cachedRun;
  if (!run) return null;
  const workflow = run.workflow || "scan";
  return workflow === currentWorkflow(study) ? run : null;
}

function invalidateCachedScan(study) {
  const workflow = study.cachedRun?.workflow || "scan";
  if (workflow === "scan") study.cachedRun = null;
}

function refreshAfterScanSettingsChange(study) {
  invalidateProposals(study);
  if (study.specificationKind !== "regex") invalidateCachedScan(study);
  study.updatedAt = new Date().toISOString();
  if (!study.bundled) saveStudy(study);
  if (study.specificationKind === "regex") {
    renderActiveStudy();
  } else {
    renderFindings(null);
    renderSource(study.document, []);
  }
}

function proposalKey(study, findingId) {
  return `${study.id}:${findingId}`;
}

function invalidateProposals(study) {
  if (study.cachedRun?.proposedFixes) delete study.cachedRun.proposedFixes;
  const prefix = `${study.id}:`;
  for (const [key, controller] of state.proposalControllers) {
    if (key.startsWith(prefix)) {
      controller.abort();
      state.proposalControllers.delete(key);
    }
  }
  for (const key of state.proposalResults.keys()) {
    if (key.startsWith(prefix)) state.proposalResults.delete(key);
  }
}

function studyThinkingBudget(study) {
  const value = study.settings.thinkingBudget;
  return Number.isInteger(value) && value >= 0 ? value : DEFAULT_THINKING_BUDGET;
}

function renderThinkingBudget(value, maximum = Math.max(1024, value)) {
  const progress = maximum ? (value / maximum) * 100 : 0;
  elements.thinkingBudgetInput.max = String(maximum);
  elements.thinkingBudgetInput.value = String(value);
  elements.thinkingBudgetInput.style.setProperty("--fill", `${progress}%`);
  elements.thinkingBudgetValue.textContent = String(value);
}

function renderControls(study) {
  elements.analysisControls.hidden = false;
  elements.analysisEmpty.hidden = true;
  elements.workflowToggle.hidden = false;
  const isRegex = study.specificationKind === "regex";
  if (!isRegex && currentWorkflow(study) === "diagnose") {
    study.settings.workflow = "scan";
  }
  const workflow = currentWorkflow(study);
  elements.scanMode.classList.toggle("active", workflow === "scan");
  elements.diagnoseMode.classList.toggle("active", workflow === "diagnose");
  elements.scanMode.setAttribute("aria-pressed", String(workflow === "scan"));
  elements.diagnoseMode.setAttribute("aria-pressed", String(workflow === "diagnose"));
  elements.diagnoseMode.disabled = !isRegex;
  elements.diagnoseMode.title = isRegex ? "" : "Diagnose requires a regex specification";
  const modelControlsApply = true;
  elements.providerControl.hidden = false;
  elements.modelControl.hidden = false;
  elements.thinkingBudgetControl.hidden = false;
  elements.thinkingBudgetInput.disabled = !modelControlsApply;
  elements.regexModelNote.hidden = !isRegex;
  elements.workflowNote.hidden = isRegex;
  elements.incidentsButton.hidden = workflow !== "diagnose";
  elements.providerDisclosure.hidden = false;
  elements.providerDisclosure.textContent = isRegex
    ? `${workflow === "diagnose" ? "Diagnosis" : "Scan"} runs locally. Generating a proposed fix sends the specification and evidence to the selected provider.`
    : "This specification and its evidence are sent to the selected model provider.";
  const provider = providerById(study.settings.provider) || state.capabilities.providers[0];
  study.settings.provider = provider.id;
  study.settings.model = selectedModel(provider, study.settings.model);
  renderProviderPicker(provider, modelControlsApply);
  renderModelPicker(provider, study.settings.model, modelControlsApply);
  const budget = studyThinkingBudget(study);
  renderThinkingBudget(budget);
  elements.taxonomyControl.hidden = false;
  elements.taxonomyButton.textContent = study.taxonomy?.name || "Attach taxonomy";
  elements.taxonomyButton.title = study.taxonomy ? "Replace taxonomy" : "Attach an optional taxonomy file";
  elements.taxonomyToggle.setAttribute("aria-checked", String(Boolean(study.taxonomy)));
  elements.taxonomyToggle.setAttribute(
    "aria-label",
    study.taxonomy ? `Detach taxonomy ${study.taxonomy.name}` : "Attach taxonomy",
  );
  elements.incidentsButton.textContent = study.incidents ? "Replace incidents" : "Attach incidents";
  elements.incidentsButton.title = study.incidents?.name || "Attach a violations JSON file";
  elements.analyzeButton.textContent = state.scanController ? "Cancel" : runActionLabel(study);
  elements.analyzeButton.disabled = workflow === "diagnose" && !study.incidents;
}

function renderEmptyWorkspace() {
  configureAuditWorkspace(false);
  state.selection = null;
  elements.scanMode.classList.toggle("active", true);
  elements.scanMode.setAttribute("aria-pressed", "true");
  elements.diagnoseMode.classList.remove("active");
  elements.diagnoseMode.setAttribute("aria-pressed", "false");
  elements.analysisEmpty.textContent = "Select a specification or choose Audit judge.";
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

async function copyText(text) {
  if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
  else fallbackCopy(text);
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
  card.tabIndex = 0;
  card.dataset.findingId = finding.id;
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
  bindFindingPreview(card, [finding.id]);
  card.addEventListener("click", () => {
    pinFindings([finding.id]);
    const source = [...document.querySelectorAll(".source-reference")]
      .find((node) => JSON.parse(node.dataset.findingIds).includes(finding.id));
    source?.scrollIntoView({behavior: "smooth", block: "center"});
  });
  card.addEventListener("keydown", (event) => {
    if (event.target !== card) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      card.click();
    }
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
    node.classList.toggle("active", active.has(node.dataset.findingId));
  });
}

function uniqueTitle(filename) {
  const existing = new Set([...state.samples, ...state.imports].map((study) => study.title));
  const base = filename.replace(/\.(yaml|yml)$/i, "");
  if (!existing.has(base)) return base;
  let suffix = 2;
  while (existing.has(`${base} ${suffix}`)) suffix += 1;
  return `${base} ${suffix}`;
}

async function readTextFile(file, pattern, message) {
  if (!pattern.test(file.name)) throw new Error(message);
  if (file.size > state.capabilities.maxRequestBytes) throw new Error("The selected file is too large.");
  try {
    return new TextDecoder("utf-8", {fatal: true}).decode(await file.arrayBuffer());
  } catch (error) {
    throw new Error("The selected file must contain valid UTF-8 text.", {cause: error});
  }
}

function readYamlFile(file) {
  return readTextFile(file, /\.ya?ml$/i, "Choose a .yaml or .yml file.");
}

async function importSpecification(file) {
  try {
    const text = await readYamlFile(file);
    const validation = await api("/api/v1/validate", {
      method: "POST",
      body: JSON.stringify({specification: {name: file.name, text}}),
    });
    const now = new Date().toISOString();
    const study = {
      id: `study:${crypto.randomUUID()}`,
      title: uniqueTitle(file.name),
      specificationName: file.name,
      specificationText: text,
      specificationKind: validation.specificationKind,
      document: validation.document,
      taxonomy: null,
      settings: defaultStudySettings(),
      incidents: null,
      cachedRun: null,
      bundled: false,
      createdAt: now,
      updatedAt: now,
    };
    state.imports.unshift(study);
    await saveStudy(study);
    await selectStudy(study);
    setNotice("Specification loaded. Scan it, or diagnose a regex specification.", "success");
  } catch (error) {
    setNotice(error.message);
  } finally {
    elements.fileInput.value = "";
  }
}

async function attachTaxonomy(file) {
  try {
    const text = await readYamlFile(file);
    const study = state.activeStudy;
    study.taxonomy = {name: file.name, text};
    invalidateProposals(study);
    invalidateCachedScan(study);
    study.updatedAt = new Date().toISOString();
    if (!study.bundled) await saveStudy(study);
    renderActiveStudy();
    setNotice("Taxonomy attached. The previous cached scan was cleared.", "success");
  } catch (error) {
    setNotice(error.message);
  } finally {
    elements.taxonomyInput.value = "";
  }
}

async function detachTaxonomy() {
  const study = state.activeStudy;
  if (!study?.taxonomy) return;
  study.taxonomy = null;
  invalidateProposals(study);
  invalidateCachedScan(study);
  study.updatedAt = new Date().toISOString();
  if (!study.bundled) await saveStudy(study);
  renderActiveStudy();
  setNotice("Taxonomy detached. The previous cached scan was cleared.", "success");
}

function toggleTaxonomy() {
  const study = state.activeStudy;
  if (!study) return;
  if (study.taxonomy) {
    detachTaxonomy();
    return;
  }
  elements.taxonomyInput.click();
}

async function attachIncidents(file) {
  try {
    const study = state.activeStudy;
    if (!study) return;
    const text = await readTextFile(file, /\.json$/i, "Choose a .json incidents file.");
    study.incidents = {name: file.name, text};
    invalidateProposals(study);
    study.cachedRun = null;
    study.settings.workflow = "diagnose";
    study.updatedAt = new Date().toISOString();
    if (!study.bundled) await saveStudy(study);
    renderActiveStudy();
    setNotice("Incidents attached. The previous cached analysis was cleared.", "success");
  } catch (error) {
    setNotice(error.message);
  } finally {
    elements.incidentsInput.value = "";
  }
}

function setWorkflow(workflow) {
  if (isAuditStudy(state.activeStudy) || !state.activeStudy) {
    state.scanController?.abort();
    state.activeStudy = null;
    state.auditReplacement = null;
    renderEmptyWorkspace();
    renderStudyLists();
    return;
  }
  const study = state.activeStudy;
  if (!study) return;
  if (workflow === "diagnose" && study.specificationKind !== "regex") {
    setNotice("Diagnosis is available for regex specifications.");
    return;
  }
  study.settings.workflow = workflow;
  renderActiveStudy();
  if (workflow === "diagnose" && !study.incidents) {
    setNotice("Attach a violations JSON file, then diagnose.");
  } else {
    setNotice("");
  }
}

async function analyzeActiveStudy() {
  const study = state.activeStudy;
  if (!study) return;
  if (isAuditStudy(study)) { await runAuditStudy(study); return; }
  if (state.scanController) {
    state.scanController.abort();
    return;
  }
  const workflow = currentWorkflow(study);
  if (workflow === "diagnose" && !study.incidents) {
    setNotice("Attach a violations JSON file, then diagnose.");
    return;
  }
  const thinkingBudget = Number.parseInt(elements.thinkingBudgetInput.value, 10);
  if (!Number.isInteger(thinkingBudget) || thinkingBudget < 0) {
    setNotice("Volume must be a non-negative integer.");
    return;
  }
  const controller = new AbortController();
  state.scanController = controller;
  invalidateProposals(study);
  const studyId = study.id;
  elements.analyzeButton.textContent = "Cancel";
  setNotice("");
  try {
    const run = workflow === "diagnose"
      ? await api("/api/v1/diagnoses", {
        method: "POST",
        body: JSON.stringify({
          specification: {name: study.specificationName, text: study.specificationText},
          violations: study.incidents,
        }),
        signal: controller.signal,
      })
      : await api("/api/v1/scans", {
        method: "POST",
        body: JSON.stringify({
          specification: {name: study.specificationName, text: study.specificationText},
          provider: study.settings.provider,
          model: study.settings.model,
          thinkingBudget,
          ...(study.taxonomy ? {taxonomy: study.taxonomy} : {}),
        }),
        signal: controller.signal,
      });
    if (state.activeStudy?.id !== studyId || controller.signal.aborted) return;
    study.settings = {
      provider: study.settings.provider,
      model: study.settings.model,
      workflow,
      thinkingBudget,
    };
    study.cachedRun = run;
    study.updatedAt = new Date().toISOString();
    if (!study.bundled) await saveStudy(study);
    renderActiveStudy();
    const verb = workflow === "diagnose" ? "Diagnosis" : "Scan";
    setNotice(`${verb} complete: ${findingCountText(run.findings.length)}.`, "success");
  } catch (error) {
    if (error.name === "AbortError") {
      setNotice(`${workflow === "diagnose" ? "Diagnosis" : "Scan"} cancelled.`);
    } else {
      setNotice(error.message);
    }
  } finally {
    if (state.scanController === controller) {
      state.scanController = null;
      elements.analyzeButton.textContent = runActionLabel(study);
    }
  }
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

function invalidateCachedRun() {
  const study = state.activeStudy;
  if (!study) return;
  const providerDefinition = providerById(study.settings.provider);
  const model = selectedModel(providerDefinition, study.settings.model);
  const thinkingBudget = Number.parseInt(elements.thinkingBudgetInput.value, 10);
  if (
    (study.settings.model && study.settings.model !== model)
    || studyThinkingBudget(study) !== thinkingBudget
  ) {
    study.settings.model = model;
    study.settings.thinkingBudget = thinkingBudget;
    refreshAfterScanSettingsChange(study);
  }
}

function updateThinkingBudget() {
  const thinkingBudget = Number.parseInt(elements.thinkingBudgetInput.value, 10);
  const maximum = Number.parseInt(elements.thinkingBudgetInput.max, 10);
  renderThinkingBudget(thinkingBudget, maximum);
  invalidateCachedRun();
}

function fallbackCopy(text) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("Clipboard access is unavailable.");
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

function setComparisonTab(tab) {
  elements.analysisGrid.dataset.activePanel = tab;
  const sourceActive = tab === "source";
  elements.sourceTab.classList.toggle("active", sourceActive);
  elements.findingsTab.classList.toggle("active", !sourceActive);
  elements.sourceTab.setAttribute("aria-selected", String(sourceActive));
  elements.findingsTab.setAttribute("aria-selected", String(!sourceActive));
  elements.sourceTab.tabIndex = sourceActive ? 0 : -1;
  elements.findingsTab.tabIndex = sourceActive ? -1 : 0;
}

function handleComparisonTabKeydown(event) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const tab = event.key === "ArrowLeft" || event.key === "Home" ? "source" : "findings";
  setComparisonTab(tab);
  (tab === "source" ? elements.sourceTab : elements.findingsTab).focus();
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
  elements.providerPickerButton.addEventListener("click", toggleProviderMenu);
  elements.modelPickerButton.addEventListener("click", toggleModelMenu);
  elements.providerMenu.addEventListener("keydown", handlePickerMenuKeydown);
  elements.modelMenu.addEventListener("keydown", handlePickerMenuKeydown);
  elements.thinkingBudgetInput.addEventListener("input", updateThinkingBudget);
  document.addEventListener("keydown", (event) => {
    trapDrawerFocus(event);
    if (event.key !== "Escape") return;
    if (!elements.providerMenu.hidden) closeProviderMenu({restoreFocus: true});
    else if (!elements.modelMenu.hidden) closeModelMenu({restoreFocus: true});
    else if (state.openDrawer) closeDrawers();
    else if (state.proposalControllers.size) {
      for (const controller of state.proposalControllers.values()) controller.abort();
    }
    else pinFindings([]);
  });
  document.addEventListener("click", (event) => {
    if (!elements.providerControl.contains(event.target)) closeProviderMenu();
    if (!elements.modelControl.contains(event.target)) closeModelMenu();
  });
  window.addEventListener("resize", () => {
    if (window.innerWidth >= 1200 && state.openDrawer) closeDrawers(false);
  });
}

async function initialize() {
  Object.assign(elements, {
    storageStatus: element("storage-status"),
    appVersion: element("app-version"),
    activeFileControl: element("active-file-control"),
    activeFilename: element("active-filename"),
    copyStatus: element("copy-status"),
    notice: element("notice"),
    sampleList: element("sample-list"),
    importList: element("import-list"),
    importsEmpty: element("imports-empty"),
    studyCount: element("study-count"),
    clearButton: element("clear-button"),
    restoreSamplesButton: element("restore-samples-button"),
    importButton: element("import-button"),
    fileInput: element("file-input"),
    studyKind: element("study-kind"),
    studyTitle: element("study-title"),
    sourceName: element("source-name"),
    analysisEmpty: element("analysis-empty"),
    analysisControls: element("analysis-controls"),
    workflowToggle: element("workflow-toggle"),
    providerControl: element("provider-control"),
    modelControl: element("model-control"),
    providerValue: element("provider-value"),
    providerPickerButton: element("provider-picker-button"),
    providerMenu: element("provider-menu"),
    modelValue: element("model-value"),
    modelPickerButton: element("model-picker-button"),
    modelColor: element("model-color"),
    modelMenu: element("model-menu"),
    thinkingBudgetControl: element("thinking-budget-control"),
    thinkingBudgetInput: element("thinking-budget-input"),
    thinkingBudgetValue: element("thinking-budget-value"),
    regexModelNote: element("regex-model-note"),
    workflowNote: element("workflow-note"),
    taxonomyControl: element("taxonomy-control"),
    taxonomyButton: element("taxonomy-button"),
    taxonomyToggle: element("taxonomy-toggle"),
    taxonomyInput: element("taxonomy-input"),
    analyzeButton: element("analyze-button"),
    scanMode: element("scan-mode"),
    diagnoseMode: element("diagnose-mode"),
    incidentsButton: element("incidents-button"),
    incidentsInput: element("incidents-input"),
    providerDisclosure: element("provider-disclosure"),
    sourceView: element("source-view"),
    findingCount: element("finding-count"),
    mobileFindingCount: element("mobile-finding-count"),
    summary: element("summary"),
    findingList: element("finding-list"),
    specificationsPanel: element("specifications-panel"),
    analysisPanel: element("analysis-panel"),
    specificationsToggle: element("specifications-toggle"),
    analysisToggle: element("analysis-toggle"),
    specificationsClose: element("specifications-close"),
    analysisClose: element("analysis-close"),
    drawerOverlay: element("drawer-overlay"),
    analysisGrid: element("analysis-grid"),
    sourceTab: element("source-tab"),
    findingsTab: element("findings-tab"),
  });
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
