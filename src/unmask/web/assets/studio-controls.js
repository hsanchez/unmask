"use strict";

function providerById(id) {
  return state.capabilities.providers.find((provider) => provider.id === id);
}

const pickers = [];

function pickerMenu(menuKey, buttonKey) {
  const picker = {
    close({restoreFocus = false} = {}) {
      const menu = elements[menuKey];
      const button = elements[buttonKey];
      if (menu.hidden) return;
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
      if (restoreFocus) button.focus();
    },
    toggle() {
      const menu = elements[menuKey];
      const button = elements[buttonKey];
      if (button.disabled) return;
      const opening = menu.hidden;
      for (const other of pickers) if (other !== picker) other.close();
      menu.hidden = !opening;
      button.setAttribute("aria-expanded", String(opening));
      if (opening) menu.querySelector('[aria-selected="true"]')?.focus();
    },
  };
  pickers.push(picker);
  return picker;
}

const providerPicker = pickerMenu("providerMenu", "providerPickerButton");
const modelPicker = pickerMenu("modelMenu", "modelPickerButton");

function pickerOption({className, selected, onChoose}) {
  const option = document.createElement("button");
  option.type = "button";
  option.className = className;
  option.setAttribute("role", "option");
  option.setAttribute("aria-selected", String(selected));
  option.addEventListener("click", onChoose);
  return option;
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

function chooseProvider(provider) {
  const study = state.activeStudy;
  if (!study) return;
  if (study.settings.provider === provider.id) {
    providerPicker.close({restoreFocus: true});
    return;
  }
  study.settings.provider = provider.id;
  study.settings.model = provider.defaultModel;
  providerPicker.close({restoreFocus: true});
  refreshAfterScanSettingsChange(study);
  renderControls(study);
}

function providerOption(provider) {
  const option = pickerOption({
    className: "picker-option",
    selected: provider.id === state.activeStudy?.settings.provider,
    onChoose: () => chooseProvider(provider),
  });
  const name = document.createElement("span");
  name.textContent = provider.id;
  option.append(name);
  if (!provider.configured) {
    const status = document.createElement("small");
    status.textContent = "not configured";
    option.append(status);
  }
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
  if (!enabled) providerPicker.close();
}

function modelColor(model) {
  return MODEL_COLORS[model] || "#63b8e7";
}

function selectedModel(provider, requestedModel) {
  return provider.models.includes(requestedModel) ? requestedModel : provider.defaultModel;
}

function chooseModel(model) {
  const study = state.activeStudy;
  if (!study) return;
  const changed = study.settings.model !== model;
  study.settings.model = model;
  modelPicker.close({restoreFocus: true});
  if (changed) {
    refreshAfterScanSettingsChange(study);
  }
  renderControls(study);
}

function modelOption(model) {
  const option = pickerOption({
    className: "picker-option model-option",
    selected: model === state.activeStudy?.settings.model,
    onChoose: () => chooseModel(model),
  });
  const color = document.createElement("span");
  color.className = "model-color";
  color.style.setProperty("--model-color", modelColor(model));
  color.setAttribute("aria-hidden", "true");
  const name = document.createElement("span");
  name.className = "model-option-name";
  name.textContent = model;
  option.append(color, name);
  return option;
}

function renderModelPicker(provider, model, enabled) {
  elements.modelValue.textContent = model;
  elements.modelColor.style.setProperty("--model-color", modelColor(model));
  elements.modelPickerButton.disabled = !enabled;
  elements.modelPickerButton.title = enabled ? `Choose model. Current model: ${model}` : model;
  elements.modelMenu.replaceChildren(...provider.models.map(modelOption));
  if (!enabled) modelPicker.close();
}

function currentWorkflow(study) {
  if (isAuditStudy(study)) return "audit_judge";
  const workflow = study.settings.workflow || "scan";
  if (!["scan", "diagnose"].includes(workflow)) throw new Error("Unsupported study workflow");
  return workflow;
}

function runActionLabel(study) {
  return {scan: "Scan", diagnose: "Diagnose", audit_judge: "Check adherence"}[currentWorkflow(study)];
}

function matchingCachedRun(study) {
  if (isAuditStudy(study)) return UnmaskAudit.cached(study, state.capabilities.judgeAudits.auditorVersion);
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
