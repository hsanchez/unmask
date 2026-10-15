const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const fixture = fs.readFileSync(path.join(root, 'tests/fixtures/judge_audit/A_correct_procedurally_adherent.json'), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {};
    this.listeners = {}; this.value = ''; this.hidden = false; this.disabled = false;
    this.style = {setProperty() {}}; this.className = ''; this._text = '';
    this.classList = {toggle: (name, active) => {
      const classes = new Set(this.className.split(' ').filter(Boolean));
      if (active) classes.add(name); else classes.delete(name);
      this.className = [...classes].join(' ');
    }, remove: (name) => this.classList.toggle(name, false)};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  get firstChild() { if (!this.children.length) this.children.push(new Element('text')); return this.children[0]; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = [...children]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  click() { if (!this.disabled) this.listeners.click?.({target: this}); }
  querySelectorAll(selector) {
    const classes = selector.split('.').filter(Boolean);
    return this.children.flatMap(child => [child, ...child.querySelectorAll(selector)])
      .filter(child => classes.every(name => child.className.split(' ').includes(name)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0]; }
  scrollIntoView() { this.scrolled = true; }
}

function studio() {
  const nodes = new Map(); const saved = new Map(); const downloads = []; const calls = [];
  const document = {getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
    createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag), addEventListener() {},
    querySelectorAll: selector => [...nodes.values()].flatMap(node => node.querySelectorAll(selector))};
  const context = vm.createContext({console, document, crypto: webcrypto, AbortController, Blob,
    URL: {createObjectURL: blob => { downloads.push(blob); return 'blob:test'; }, revokeObjectURL() {}},
    window: {setTimeout: callback => callback()}, structuredClone, JSON, Symbol,
    saved, downloads, calls, fixture, TextEncoder});
  for (const name of ['audit-state.js', 'audit.js', 'app.js']) vm.runInContext(fs.readFileSync(path.join(root, 'src/unmask/web/assets', name), 'utf8'), context);
  vm.runInContext(`
    for (const [key, id] of Object.entries({
      sourceView:'source-view', studyTitle:'study-title', studyKind:'study-kind', activeFileControl:'active-file-control',
      analysisEmpty:'analysis-empty', analysisControls:'analysis-controls', workflowToggle:'workflow-toggle',
      scanMode:'scan-mode', diagnoseMode:'diagnose-mode', providerControl:'provider-control', modelControl:'model-control',
      thinkingBudgetControl:'thinking-budget-control', taxonomyControl:'taxonomy-control', incidentsButton:'incidents-button',
      regexModelNote:'regex-model-note', workflowNote:'workflow-note', providerDisclosure:'provider-disclosure',
      analyzeButton:'analyze-button', sourceTab:'source-tab', findingsTab:'findings-tab', sourceName:'source-name',
      summary:'summary', findingCount:'finding-count', mobileFindingCount:'mobile-finding-count', findingList:'finding-list',
      sampleList:'sample-list', importList:'import-list', importsEmpty:'imports-empty', restoreSamplesButton:'restore-samples-button',
      clearButton:'clear-button', studyCount:'study-count', notice:'notice', analysisGrid:'analysis-grid',
      copyStatus:'copy-status', thinkingBudgetInput:'thinking-budget-input'
    })) elements[key] = element(id);
    state.capabilities = {judgeAudits:{auditorVersion:'1'}, maxRequestBytes:1000000};
    storageRequest = async (mode, action) => action({getAll: () => [...saved.values()], put: record => saved.set(record.id, structuredClone(record))});
    closeDrawers = () => {};
    function validation(text) {
      const data = JSON.parse(text);
      return {schemaVersion:1, auditorVersion:'1', workflow:'audit_judge', name:data.id, contentHash:text,
        case:data, documents:[['protocol',data.protocol.text],['context',data.context],['response',data.response],['rationale',data.rationale]]
          .map(([id, rawText]) => ({id,name:id,rawText}))};
    }
    function report(text) {
      return {...validation(text), status:'completed', summary:{status:'OK',overall:'UNKNOWN',coverage:'UNKNOWN',procedure:'UNKNOWN',grounding:'UNKNOWN',consistency:'COMPATIBLE',entailment:'UNDERDETERMINED'},
        audit:{}, checks:[{id:'verdict',dimension:'entailment',summary:'Verdict',status:'UNDERDETERMINED',proof:{status:'UNDERDETERMINED'},sourceReferences:[{documentId:'rationale',start:0,end:5}]}],unknownAtoms:['j2'],limitations:['Bounded replay']};
    }
    api = async (route, options) => {
      calls.push(route);
      if (route === '/api/v1/judge-audits/example') return {name:'example.json',text:fixture};
      const input = JSON.parse(options.body);
      return route.endsWith('/validate') ? validation(input.case.text) : report(input.case.text);
    };
  `, context);
  return {context, nodes, saved, downloads, calls, run: source => vm.runInContext(source, context)};
}

test('legacy records normalize without losing scan settings; unknown records are rejected', () => {
  const app = studio();
  assert.equal(app.run(`UnmaskAudit.normalize({specificationText:'rules: []',settings:{workflow:'diagnose'}}).settings.workflow`), 'diagnose');
  assert.equal(app.run(`UnmaskAudit.normalize({recordType:'future'})`), null);
  assert.equal(app.run(`UnmaskAudit.normalize({recordType:'judge_audit',recordVersion:2,auditCaseText:'{}'})`), null);
});

test('audit import rejects non-object JSON with a clear message', () => {
  const app = studio();
  for (const text of ['null', '[]', 'true', '123', '"text"']) {
    assert.throws(() => app.run(`UnmaskAudit.input(${JSON.stringify(text)})`), /must be a JSON object/);
  }
});

test('new case opens an importer while retaining the saved active case', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  app.run('bindAuditEvents()');
  app.nodes.get('audit-new').click();

  assert.equal(app.run('state.activeStudy'), null);
  assert.equal(app.saved.size, 1);
  assert.equal(app.nodes.get('audit-import-panel').hidden, false);
});

test('keeping current case discards an in-flight replacement validation', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  app.run(`
    bindAuditEvents();
    const originalStudy = state.activeStudy;
    startAuditImport(true);
    let finishValidation;
    api = () => new Promise(resolve => { finishValidation = resolve; });
    let pending = importAuditText(fixture);
  `);
  app.nodes.get('audit-import-cancel').click();
  app.run('finishValidation(validation(fixture))'); await app.run('pending');

  assert.equal(app.run('state.activeStudy === originalStudy'), true);
  assert.equal(app.saved.size, 1);
  assert.equal(app.nodes.get('audit-import-panel').hidden, true);
});

test('replacement save disables cancellation until persistence completes', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  const identifier = app.run('state.activeStudy.id');
  app.run(`
    bindAuditEvents(); startAuditImport(true);
    const originalStudy = state.activeStudy;
    let releaseSave, markSaving;
    let saving = new Promise(resolve => { markSaving = resolve; });
    const persistStudy = saveStudy;
    saveStudy = async study => {
      await new Promise(resolve => { releaseSave = resolve; markSaving(); });
      await persistStudy(study);
    };
    let pending = importAuditText(JSON.stringify({...JSON.parse(fixture), id:'replacement-B'}));
  `);
  await app.run('saving');
  const cancel = app.nodes.get('audit-import-cancel');
  assert.equal(cancel.disabled, true);
  cancel.click();
  assert.equal(app.run('state.activeStudy === originalStudy'), true);
  assert.equal(app.run('state.auditReplacement === originalStudy'), true);
  assert.equal(JSON.parse(app.saved.get(identifier).auditCaseText).id, JSON.parse(fixture).id);

  app.run('releaseSave()'); await app.run('pending');

  assert.equal(app.run('state.activeStudy.id'), identifier);
  assert.equal(app.run('state.activeStudy.validation.name'), 'replacement-B');
  assert.equal(JSON.parse(app.saved.get(identifier).auditCaseText).id, 'replacement-B');
  assert.equal(cancel.disabled, false);
});

test('failed replacement save retains the original case and restores cancellation', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  const identifier = app.run('state.activeStudy.id');
  app.run(`
    bindAuditEvents(); startAuditImport(true);
    const originalStudy = state.activeStudy;
    saveStudy = async () => { throw new Error('injected storage failure'); };
  `);

  await assert.rejects(app.run('importAuditText(fixture)'), /injected storage failure/);

  assert.equal(app.nodes.get('audit-import-cancel').disabled, false);
  assert.equal(app.run('state.activeStudy === originalStudy'), true);
  assert.equal(app.run('state.imports[0] === originalStudy'), true);
  assert.equal(app.saved.get(identifier).auditCaseText, fixture);
  app.nodes.get('audit-import-cancel').click();
  assert.equal(app.run('state.activeStudy === originalStudy'), true);
  assert.equal(app.nodes.get('audit-import-panel').hidden, true);
});

test('delayed example loading preserves newer navigation', async () => {
  const app = studio();
  app.run(`
    bindAuditEvents(); startAuditImport();
    let finishExample;
    api = () => new Promise(resolve => { finishExample = resolve; });
    let pending = element('audit-example').listeners.click();
    startAuditImport();
    finishExample({name:'example.json', text:fixture});
  `);
  await app.run('pending');

  assert.equal(app.saved.size, 0);
  assert.equal(app.run('state.activeStudy'), null);
});

test('delayed file reading preserves newer navigation', async () => {
  const app = studio();
  app.run(`
    bindAuditEvents(); startAuditImport();
    let finishRead;
    readTextFile = () => new Promise(resolve => { finishRead = resolve; });
    let pending = element('audit-file').listeners.change({target:{files:[{name:'case.json'}], value:'case.json'}});
    startAuditImport();
    finishRead(fixture);
  `);
  await app.run('pending');

  assert.equal(app.saved.size, 0);
  assert.equal(app.run('state.activeStudy'), null);
});

test('import validates, persists exact case and renders read-only sources without provider controls', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  assert.equal(app.saved.size, 1);
  assert.equal(app.run('state.activeStudy.auditCaseText'), fixture);
  assert.equal(app.nodes.get('source-view').textContent, JSON.parse(fixture).protocol.text);
  assert.equal(app.nodes.get('provider-control').hidden, true);
  assert.equal(app.nodes.get('taxonomy-control').hidden, true);
});

test('source renderer filters document IDs and converts Unicode offsets', () => {
  const app = studio();
  app.run(`renderSource({id:'context',rawText:'A🚫BC'}, [{id:'right',sourceReferences:[{documentId:'context',start:1,end:2}]},{id:'wrong',sourceReferences:[{documentId:'rationale',start:0,end:4}]}])`);
  const spans = app.nodes.get('source-view').querySelectorAll('.source-reference');
  assert.equal(spans.length, 1); assert.equal(spans[0].textContent, '🚫');
  assert.deepEqual(JSON.parse(spans[0].dataset.findingIds), ['right']);
});

test('annotation evidence navigation switches to the matching source document', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  app.run(`state.activeStudy.auditView='annotations'; renderAuditSource(state.activeStudy)`);
  assert.match(app.nodes.get('audit-annotations').textContent, /hand-annotated-replay/);
  app.run(`showAuditEvidence(state.activeStudy,{documentId:'context',start:0,end:state.activeStudy.validation.case.context.length},'annotation:2')`);
  assert.equal(app.run('state.activeStudy.activeDocument'), 'context');
  assert.equal(app.nodes.get('source-view').textContent, JSON.parse(fixture).context);
  assert.equal(app.nodes.get('audit-annotations').hidden, true);
});

test('evidence links scroll to and retain only the clicked range in a document', async () => {
  const app = studio();
  await app.run(`
    const data = JSON.parse(fixture);
    data.rationale = 'a🚫bc def';
    data.findings = [{...data.findings[0], evidence:[
      {source:'rationale', start:1, end:2, quote:'🚫'},
      {source:'rationale', start:5, end:8, quote:'def'},
    ]}];
    importAuditText(JSON.stringify(data));
  `);
  app.run(`
    const references = state.activeStudy.validation.case.findings[0].evidence.map(evidence => ({...evidence, documentId:evidence.source}));
    const links = auditEvidenceButtons(state.activeStudy, references, 'annotation:0');
    state.hoveredFindingIds = ['annotation:0'];
    links.children[1].click();
    clearPreview();
  `);

  const spans = app.nodes.get('source-view').querySelectorAll('.source-reference');
  assert.equal(spans.length, 2);
  assert.equal(spans[0].textContent, '🚫');
  assert.equal(spans[0].className.includes('active'), false);
  assert.equal(spans[0].scrolled, undefined);
  assert.equal(spans[1].textContent, 'def');
  assert.equal(spans[1].className.includes('active'), true);
  assert.equal(spans[1].scrolled, true);

  app.run('links.children[0].click()');
  const selected = app.nodes.get('source-view').querySelectorAll('.source-reference.active');
  assert.equal(selected.length, 1);
  assert.equal(selected[0].textContent, '🚫');
  assert.equal(selected[0].scrolled, true);
});

test('audit execution exports portable case and report with separate uncertainty dimensions', async () => {
  const app = studio(); await app.run('importAuditText(fixture)'); await app.run('analyzeActiveStudy()');
  assert.equal(app.calls.at(-1), '/api/v1/judge-audits');
  assert.match(app.nodes.get('finding-list').textContent, /UNDERDETERMINED/);
  assert.doesNotMatch(app.nodes.get('finding-list').textContent, /Generate fix|No supported specification defects/);
  app.run('exportAudit(false); exportAudit(true)');
  assert.deepEqual(JSON.parse(await app.downloads[0].text()), JSON.parse(fixture));
  assert.equal(JSON.parse(await app.downloads[1].text()).workflow, 'audit_judge');
});

test('reload revalidates a persisted case and retains only compatible cached results', async () => {
  const app = studio(); await app.run('importAuditText(fixture)'); await app.run('analyzeActiveStudy()');
  await app.run('loadImports();'); await app.run('selectStudy(state.imports[0])');
  assert.equal(app.run('Boolean(UnmaskAudit.cached(state.activeStudy,"1"))'), true);
  app.run('state.capabilities.judgeAudits.auditorVersion="2"');
  await app.run('validateAuditStudy(state.activeStudy)');
  assert.equal(app.run('state.activeStudy.cachedRun'), null);
});

test('replacement preserves study identity while invalidating report and evidence view', async () => {
  const app = studio(); await app.run('importAuditText(fixture)'); await app.run('analyzeActiveStudy()');
  const id = app.run('state.activeStudy.id');
  await app.run(`state.auditReplacement=state.activeStudy; importAuditText(fixture.replace('Alice joined ACME in 2024.', 'Alice joined ACME in 2024.'))`);
  assert.equal(app.run('state.activeStudy.id'), id);
  assert.equal(app.run('state.activeStudy.cachedRun'), null);
  assert.equal(app.saved.size, 1);
});

test('late audit responses do not overwrite a replaced or deselected case', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  app.run(`let resolveAudit; api=()=>new Promise(resolve=>{resolveAudit=resolve}); let pending=runAuditStudy(state.activeStudy); let oldStudy=state.activeStudy; state.activeStudy=null; resolveAudit(report(fixture));`);
  await app.run('pending');
  assert.equal(app.run('oldStudy.cachedRun'), null);
});

test('report import uses case only and requires a fresh audit', async () => {
  const app = studio();
  await app.run('importAuditText(JSON.stringify(report(fixture)))');
  assert.equal(app.run('state.activeStudy.cachedRun'), null);
  assert.match(app.nodes.get('notice').textContent, /fresh audit/);
});

test('scan and diagnose still dispatch to their existing endpoints', async () => {
  const app = studio();
  app.run(`renderActiveStudy=()=>{}; elements.thinkingBudgetInput.value='128'; api=async(route)=>{calls.push(route);return {findings:[]}}; state.activeStudy={id:'legacy',specificationName:'a.yaml',specificationText:'x',settings:{workflow:'scan'},incidents:{name:'i.json',text:'[]'}}`);
  await app.run('analyzeActiveStudy()');
  assert.equal(app.calls.at(-1), '/api/v1/scans');
  app.run('state.activeStudy.settings.workflow="diagnose"'); await app.run('analyzeActiveStudy()');
  assert.equal(app.calls.at(-1), '/api/v1/diagnoses');
});

test('audit event bindings load the example and change review tabs', async () => {
  const app = studio(); app.run('bindAuditEvents()');
  await app.nodes.get('audit-example').listeners.click();
  app.nodes.get('audit-annotations-button').click();
  assert.equal(app.run('state.activeStudy.auditView'), 'annotations');
  app.nodes.get('audit-sources-button').click();
  assert.equal(app.run('state.activeStudy.auditView'), 'sources');
});

for (const [name, navigation] of [
  ['new import', 'startAuditImport()'],
  ['replacement', 'startAuditImport(true)'],
  ['Scan workflow', 'setWorkflow("scan")'],
]) {
  test(`delayed audit persistence preserves newer ${name} navigation`, async () => {
    const app = studio(); await app.run('importAuditText(fixture)');
    app.run(`
      let finishSave, markSaving;
      let saving = new Promise(resolve => { markSaving = resolve; });
      saveStudy = () => new Promise(resolve => { finishSave = resolve; markSaving(); });
      let pending = runAuditStudy(state.activeStudy);
    `);
    await app.run('saving');
    app.run(navigation);
    const title = app.nodes.get('study-title').textContent;
    const importHidden = app.nodes.get('audit-import-panel').hidden;
    const activeStudy = app.run('state.activeStudy');
    app.run('finishSave()'); await app.run('pending');
    assert.equal(app.nodes.get('study-title').textContent, title);
    assert.equal(app.nodes.get('audit-import-panel').hidden, importHidden);
    assert.equal(app.run('state.activeStudy'), activeStudy);
    assert.doesNotMatch(app.nodes.get('notice').textContent, /Audit complete/);
  });
}

test('cancel during audit persistence does not publish a completion notice', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  app.run(`
    let finishSave, markSaving;
    let saving = new Promise(resolve => { markSaving = resolve; });
    saveStudy = () => new Promise(resolve => { finishSave = resolve; markSaving(); });
    let pending = runAuditStudy(state.activeStudy);
  `);
  await app.run('saving');
  await app.run('runAuditStudy(state.activeStudy)');
  app.run('finishSave()'); await app.run('pending');
  assert.doesNotMatch(app.nodes.get('notice').textContent, /Audit complete/);
  assert.equal(app.nodes.get('analyze-button').textContent, 'Run audit');
});

test('delayed import persistence does not select a case after newer navigation', async () => {
  const app = studio();
  app.run(`
    let finishSave, markSaving;
    let saving = new Promise(resolve => { markSaving = resolve; });
    const persistStudy = saveStudy;
    saveStudy = async study => {
      await new Promise(resolve => { finishSave = resolve; markSaving(); });
      await persistStudy(study);
    };
    let pending = importAuditText(fixture);
  `);
  await app.run('saving');
  app.run('startAuditImport()');
  app.run('finishSave()'); await app.run('pending');
  assert.equal(app.run('state.activeStudy'), null);
  assert.equal(app.nodes.get('audit-import-panel').hidden, false);
  assert.equal(app.nodes.get('study-title').textContent, 'Audit a judge evaluation');
  assert.doesNotMatch(app.nodes.get('notice').textContent, /Case loaded/);
  assert.equal(app.saved.size, 1);
});
