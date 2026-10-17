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

function bindElements() {
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

async function copyText(text) {
  if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
  else fallbackCopy(text);
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
