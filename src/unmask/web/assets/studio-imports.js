"use strict";

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
