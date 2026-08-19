"use strict";

const DATABASE_NAME = "unmask-studio";
const DATABASE_VERSION = 1;
const STUDY_STORE = "studies";

const state = {
  capabilities: null,
  samples: [],
  imports: [],
  activeStudy: null,
  hoveredFindingIds: [],
  pinnedFindingIds: [],
  scanController: null,
  database: null,
  storageAvailable: true,
};

const elements = {};

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
  state.imports = Array.isArray(records)
    ? records
      .filter((study) => study && typeof study.id === "string" && typeof study.updatedAt === "string")
      .map((study) => ({
        ...study,
        settings: {
          provider: "gemini",
          model: "",
          workflow: "scan",
          ...(study.settings || {}),
        },
        incidents: study.incidents || null,
      }))
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    : [];
}

async function saveStudy(study) {
  if (!state.storageAvailable) return;
  try {
    await storageRequest("readwrite", (store) => store.put(study));
  } catch (error) {
    state.storageAvailable = false;
    elements.storageStatus.textContent = "Local persistence unavailable";
    setNotice(`The study remains available for this session. ${error.message}`);
  }
}

async function deleteStoredStudy(id) {
  await storageRequest("readwrite", (store) => store.delete(id));
}

async function clearStoredStudies() {
  await storageRequest("readwrite", (store) => store.clear());
}

function studyButton(study, bundled) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "study-card";
  button.dataset.studyId = study.id;
  button.setAttribute("role", "listitem");
  const title = document.createElement("span");
  title.className = "study-title";
  title.textContent = study.title;
  const metadata = document.createElement("span");
  metadata.className = "study-meta";
  metadata.textContent = bundled ? "Bundled example" : study.specificationKind.replace("_", " ");
  button.append(title, metadata);
  button.addEventListener("click", () => selectStudy(study));
  return button;
}

function renderStudyLists() {
  elements.sampleList.replaceChildren(...state.samples.map((study) => studyButton(study, true)));
  elements.importList.replaceChildren(...state.imports.map((study) => studyButton(study, false)));
  elements.importsEmpty.hidden = state.imports.length > 0;
  elements.clearButton.hidden = state.imports.length === 0;
  document.querySelectorAll(".study-card").forEach((button) => {
    button.classList.toggle("active", button.dataset.studyId === state.activeStudy?.id);
  });
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
    settings: {provider: "gemini", model: "", workflow: "scan"},
    incidents: sample.incidentsText
      ? {name: sample.incidentsName, text: sample.incidentsText}
      : null,
    cachedRun: null,
    bundled: true,
  };
}

async function ensureValidated(study) {
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
  try {
    state.scanController?.abort();
    state.scanController = null;
    elements.analyzeButton.textContent = runActionLabel(study);
    setNotice("");
    state.activeStudy = await ensureValidated(study);
    state.hoveredFindingIds = [];
    state.pinnedFindingIds = [];
    renderStudyLists();
    renderActiveStudy();
  } catch (error) {
    setNotice(error.message);
  }
}

function providerById(id) {
  return state.capabilities.providers.find((provider) => provider.id === id);
}

function currentWorkflow(study) {
  return study.settings.workflow === "diagnose" ? "diagnose" : "scan";
}

function runActionLabel(study) {
  return currentWorkflow(study) === "diagnose" ? "Diagnose" : "Scan";
}

function matchingCachedRun(study) {
  const run = study.cachedRun;
  if (!run) return null;
  const workflow = run.workflow || "scan";
  return workflow === currentWorkflow(study) ? run : null;
}

function renderControls(study) {
  elements.analysisControls.hidden = false;
  const isRegex = study.specificationKind === "regex";
  const diagnose = currentWorkflow(study) === "diagnose";
  if (!isRegex && diagnose) {
    study.settings.workflow = "scan";
  }
  const workflow = currentWorkflow(study);
  elements.scanMode.classList.toggle("active", workflow === "scan");
  elements.diagnoseMode.classList.toggle("active", workflow === "diagnose");
  elements.diagnoseMode.disabled = !isRegex;
  elements.providerControl.hidden = isRegex || workflow === "diagnose";
  elements.modelControl.hidden = isRegex || workflow === "diagnose";
  elements.regexModelNote.hidden = !isRegex || workflow === "diagnose";
  elements.incidentsButton.hidden = workflow !== "diagnose";
  elements.providerSelect.disabled = false;
  elements.modelInput.disabled = false;
  elements.providerDisclosure.hidden = isRegex || workflow === "diagnose";
  const provider = providerById(study.settings.provider) || state.capabilities.providers[0];
  elements.providerSelect.value = provider.id;
  elements.modelInput.value = study.settings.model || provider.defaultModel;
  elements.taxonomyButton.hidden = workflow === "diagnose";
  elements.taxonomyButton.textContent = study.taxonomy ? "Replace taxonomy" : "Attach taxonomy";
  elements.taxonomyButton.title = study.taxonomy?.name || "Attach an optional taxonomy file";
  elements.incidentsButton.textContent = study.incidents ? "Replace incidents" : "Attach incidents";
  elements.incidentsButton.title = study.incidents?.name || "Attach a violations JSON file";
  elements.analyzeButton.textContent = state.scanController ? "Cancel" : runActionLabel(study);
  elements.analyzeButton.disabled = workflow === "diagnose" && !study.incidents;
}

function renderEmptyWorkspace() {
  elements.studyKind.textContent = "No specification loaded";
  elements.studyTitle.textContent = "Load a file to scan or diagnose";
  elements.sourceName.textContent = "";
  elements.renameButton.hidden = true;
  elements.deleteButton.hidden = true;
  elements.analysisControls.hidden = true;
  elements.providerDisclosure.hidden = true;
  elements.sourceView.replaceChildren();
  const placeholder = document.createElement("span");
  placeholder.className = "source-placeholder";
  placeholder.textContent = "Load a specification, then scan or diagnose it.";
  elements.sourceView.append(placeholder);
  renderFindings(null);
}

function renderActiveStudy() {
  const study = state.activeStudy;
  if (!study) {
    renderEmptyWorkspace();
    return;
  }
  elements.studyKind.textContent = study.specificationKind.replace("_", " ");
  elements.studyTitle.textContent = study.title;
  elements.sourceName.textContent = study.specificationName;
  elements.renameButton.hidden = study.bundled;
  elements.deleteButton.hidden = study.bundled;
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
  const references = [];
  for (const finding of findings) {
    for (const reference of finding.sourceReferences || []) {
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
  elements.findingList.replaceChildren(...findings.map(findingCard));
  applyHighlights();
}

function findingCard(finding) {
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
  severity.textContent = `${finding.severity} severity`;
  top.append(type, severity);
  const title = document.createElement("h3");
  title.textContent = finding.summary;
  const rationale = document.createElement("p");
  rationale.textContent = finding.rationale;
  const tags = document.createElement("div");
  tags.className = "rule-tags";
  for (const ruleId of finding.ruleIds || []) {
    const tag = document.createElement("span");
    tag.className = "rule-tag";
    tag.textContent = ruleId;
    tags.append(tag);
  }
  card.append(top, title, rationale, tags);
  bindFindingPreview(card, [finding.id]);
  card.addEventListener("click", () => {
    pinFindings([finding.id]);
    const source = [...document.querySelectorAll(".source-reference")]
      .find((node) => JSON.parse(node.dataset.findingIds).includes(finding.id));
    source?.scrollIntoView({behavior: "smooth", block: "center"});
  });
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      card.click();
    }
  });
  return card;
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

function pinFindings(ids) {
  state.pinnedFindingIds = ids;
  applyHighlights();
}

function applyHighlights() {
  const active = new Set(state.hoveredFindingIds.length ? state.hoveredFindingIds : state.pinnedFindingIds);
  document.querySelectorAll(".source-reference").forEach((node) => {
    const ids = JSON.parse(node.dataset.findingIds);
    node.classList.toggle("active", ids.some((id) => active.has(id)));
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
      settings: {provider: "gemini", model: "", workflow: "scan"},
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
    study.cachedRun = null;
    study.updatedAt = new Date().toISOString();
    if (!study.bundled) await saveStudy(study);
    renderActiveStudy();
    setNotice("Taxonomy attached. The previous cached analysis was cleared.", "success");
  } catch (error) {
    setNotice(error.message);
  } finally {
    elements.taxonomyInput.value = "";
  }
}

async function attachIncidents(file) {
  try {
    const study = state.activeStudy;
    if (!study) return;
    const text = await readTextFile(file, /\.json$/i, "Choose a .json incidents file.");
    study.incidents = {name: file.name, text};
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
  if (state.scanController) {
    state.scanController.abort();
    return;
  }
  const workflow = currentWorkflow(study);
  if (workflow === "diagnose" && !study.incidents) {
    setNotice("Attach a violations JSON file, then diagnose.");
    return;
  }
  const controller = new AbortController();
  state.scanController = controller;
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
          provider: elements.providerSelect.value,
          model: elements.modelInput.value.trim(),
          thinkingBudget: 128,
          ...(study.taxonomy ? {taxonomy: study.taxonomy} : {}),
        }),
        signal: controller.signal,
      });
    if (state.activeStudy?.id !== studyId || controller.signal.aborted) return;
    study.settings = {
      provider: elements.providerSelect.value,
      model: elements.modelInput.value.trim(),
      workflow,
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

async function renameActiveStudy() {
  const study = state.activeStudy;
  if (!study || study.bundled) return;
  const title = window.prompt("Study name", study.title)?.trim();
  if (!title || title === study.title) return;
  study.title = title;
  study.updatedAt = new Date().toISOString();
  await saveStudy(study);
  renderStudyLists();
  renderActiveStudy();
}

async function deleteActiveStudy() {
  const study = state.activeStudy;
  if (!study || study.bundled) return;
  if (!window.confirm(`Delete “${study.title}” from this browser?`)) return;
  await deleteStoredStudy(study.id);
  state.imports = state.imports.filter((item) => item.id !== study.id);
  state.activeStudy = null;
  renderStudyLists();
  renderEmptyWorkspace();
}

async function clearImports() {
  if (!state.imports.length || !window.confirm("Delete every imported study from this browser?")) return;
  await clearStoredStudies();
  state.imports = [];
  if (!state.activeStudy?.bundled) state.activeStudy = null;
  renderStudyLists();
  if (state.activeStudy) await selectStudy(state.activeStudy);
  else renderEmptyWorkspace();
}

function invalidateCachedRun() {
  const study = state.activeStudy;
  if (!study) return;
  const provider = elements.providerSelect.value;
  const model = elements.modelInput.value.trim();
  if (study.settings.provider !== provider || (study.settings.model && study.settings.model !== model)) {
    study.cachedRun = null;
    renderFindings(null);
    renderSource(study.document, []);
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
  bindFileInput(elements.importButton, elements.fileInput, importSpecification);
  bindFileInput(elements.taxonomyButton, elements.taxonomyInput, attachTaxonomy);
  bindFileInput(elements.incidentsButton, elements.incidentsInput, attachIncidents);
  elements.scanMode.addEventListener("click", () => setWorkflow("scan"));
  elements.diagnoseMode.addEventListener("click", () => setWorkflow("diagnose"));
  elements.analyzeButton.addEventListener("click", analyzeActiveStudy);
  elements.renameButton.addEventListener("click", renameActiveStudy);
  elements.deleteButton.addEventListener("click", deleteActiveStudy);
  elements.clearButton.addEventListener("click", clearImports);
  elements.providerSelect.addEventListener("change", () => {
    const provider = providerById(elements.providerSelect.value);
    elements.modelInput.value = provider.defaultModel;
    invalidateCachedRun();
  });
  elements.modelInput.addEventListener("input", invalidateCachedRun);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") pinFindings([]);
  });
}

async function initialize() {
  Object.assign(elements, {
    storageStatus: element("storage-status"),
    notice: element("notice"),
    sampleList: element("sample-list"),
    importList: element("import-list"),
    importsEmpty: element("imports-empty"),
    clearButton: element("clear-button"),
    importButton: element("import-button"),
    fileInput: element("file-input"),
    studyKind: element("study-kind"),
    studyTitle: element("study-title"),
    sourceName: element("source-name"),
    renameButton: element("rename-button"),
    deleteButton: element("delete-button"),
    analysisControls: element("analysis-controls"),
    providerControl: element("provider-control"),
    modelControl: element("model-control"),
    providerSelect: element("provider-select"),
    modelInput: element("model-input"),
    regexModelNote: element("regex-model-note"),
    taxonomyButton: element("taxonomy-button"),
    taxonomyInput: element("taxonomy-input"),
    analyzeButton: element("analyze-button"),
    scanMode: element("scan-mode"),
    diagnoseMode: element("diagnose-mode"),
    incidentsButton: element("incidents-button"),
    incidentsInput: element("incidents-input"),
    providerDisclosure: element("provider-disclosure"),
    sourceView: element("source-view"),
    findingCount: element("finding-count"),
    summary: element("summary"),
    findingList: element("finding-list"),
  });
  bindEvents();
  try {
    const [capabilities, samplesPayload] = await Promise.all([api("/api/v1/capabilities"), api("/api/v1/samples")]);
    state.capabilities = capabilities;
    state.samples = samplesPayload.samples.map(normalizeSample);
    elements.providerSelect.replaceChildren(...capabilities.providers.map((provider) => {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = `${provider.id}${provider.configured ? "" : " · not configured"}`;
      return option;
    }));
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
