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
      copyStatus:'copy-status', thinkingBudgetInput:'thinking-budget-input', storageStatus:'storage-status'
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

test('audit import uses concise guidance without repeating the input requirements', () => {
  const app = studio();
  app.run('startAuditImport()');
  assert.equal(app.nodes.get('analysis-empty').textContent, 'Local audit · no model provider');
  assert.equal(app.nodes.get('finding-list').textContent, 'Load a case to view audit results.');
  assert.equal(app.nodes.get('audit-source-controls').hidden, true);
});

test('loaded audit uses compact labels and restores specification headings when leaving', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  assert.equal(app.nodes.get('provider-disclosure').textContent, 'Local audit · no model provider');
  assert.equal(app.nodes.get('audit-verdict').textContent, `Judge verdict: ${JSON.parse(fixture).verdict} · read-only`);
  assert.equal(app.nodes.get('finding-list').textContent, 'Run audit to inspect this case.');
  app.run('configureAuditWorkspace(false)');
  assert.equal(app.nodes.get('source-heading').textContent, 'specification(source)');
  assert.equal(app.nodes.get('findings-heading').textContent, 'specification bugs(structure)');
  assert.equal(app.nodes.get('audit-import-panel').hidden, true);
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

test('requirement cards combine procedure and coverage without losing either proof or status', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  app.run(`
    const result = report(fixture);
    const reference = {documentId:'rationale',start:0,end:5};
    result.checks = [
      {id:'procedure:first', dimension:'procedure', summary:'Same title', status:'ENTAILED', proof:{status:'ENTAILED'}, sourceReferences:[reference, reference]},
      {id:'coverage:first', dimension:'coverage', summary:'Same title', status:'REFUTED', proof:{status:'REFUTED'}, sourceReferences:[reference]},
      {id:'procedure:second', dimension:'procedure', summary:'Same title', status:'ENTAILED', proof:{status:'ENTAILED'}, sourceReferences:[reference]},
    ];
    state.activeStudy.cachedRun = result;
    renderAuditResults(state.activeStudy);
  `);
  const cards = app.nodes.get('finding-list').querySelectorAll('.audit-check');
  assert.equal(cards.length, 2);
  assert.match(cards[0].textContent, /procedure · ENTAILED \/ coverage · REFUTED/);
  assert.match(cards[0].textContent, /Not established by observable evidence/);
  assert.equal(cards[0].querySelectorAll('.text-button').length, 1);
  assert.equal(cards[0].querySelectorAll('.audit-proof').length, 1);
  assert.match(cards[0].querySelector('.audit-json').textContent, /"procedure:first"/);
  assert.match(cards[0].querySelector('.audit-json').textContent, /"coverage:first"/);
  assert.equal(app.run('state.activeStudy.cachedRun.checks.length'), 3);
  cards[0].querySelector('.text-button').click();
  assert.equal(app.run('state.pinnedFindingIds[0]'), 'procedure:first');
});

test('evidence deduplication retains different ranges and source documents', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  assert.equal(app.run(`auditEvidenceButtons(state.activeStudy, [
    {documentId:'rationale',start:0,end:5},
    {documentId:'rationale',start:0,end:5,annotationId:'other'},
    {documentId:'rationale',start:1,end:5},
    {documentId:'context',start:0,end:5},
  ], 'verdict').children.length`), 3);
});

test('grounding cards identify distinct claims even when their evidence is identical', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  app.run(`
    const result = report(fixture);
    result.checks = [['j1','c1'],['j2','c2']].map((atoms, index) => ({
      id:'grounding:' + index, dimension:'grounding', summary:'Compare judge finding with independent grounding',
      atoms, status:'ENTAILED', proof:{status:'ENTAILED'}, sourceReferences:[],
    }));
    state.activeStudy.cachedRun = result;
    renderAuditResults(state.activeStudy);
  `);
  const cards = app.nodes.get('finding-list').querySelectorAll('.audit-check');
  assert.equal(cards.length, 2);
  assert.match(cards[0].children[0].textContent, /joining ACME/);
  assert.match(cards[1].children[0].textContent, /CTO role/);
});

test('audit limitations remain available in one collapsed section', async () => {
  const app = studio(); await app.run('importAuditText(fixture)'); await app.run('analyzeActiveStudy()');
  const sections = app.nodes.get('finding-list').querySelectorAll('.audit-proof');
  const limitations = sections.at(-1);
  assert.equal(limitations.tagName, 'details');
  assert.equal(limitations.children[0].textContent, 'Scope and limitations');
  assert.match(limitations.textContent, /Bounded replay/);
  assert.equal(limitations.attributes.open, undefined);
});

test('grouped cards preview both checks and stay active when coverage evidence is selected', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  app.run(`
    const result = report(fixture);
    result.checks = ['procedure','coverage'].map((dimension, index) => ({
      id:dimension + ':first', dimension, summary:'Requirement', status:'ENTAILED', proof:{status:'ENTAILED'},
      sourceReferences:[{documentId:'rationale',start:index * 5,end:index * 5 + 5}],
    }));
    state.activeStudy.cachedRun = result;
    renderAuditWorkspace(state.activeStudy);
  `);
  const card = app.nodes.get('finding-list').querySelector('.audit-check');
  card.listeners.mouseenter();
  assert.deepEqual(JSON.parse(app.run('JSON.stringify(state.hoveredFindingIds)')), ['procedure:first', 'coverage:first']);
  card.listeners.mouseleave();
  card.querySelectorAll('.text-button')[1].click();
  assert.equal(app.run('state.pinnedFindingIds[0]'), 'coverage:first');
  assert.equal(card.className.includes('active'), true);
  card.listeners.keydown({target:card, key:'Enter', preventDefault(){}});
  assert.deepEqual(JSON.parse(app.run('JSON.stringify(state.pinnedFindingIds)')), ['procedure:first', 'coverage:first']);
});

test('grouped card activation navigates to coverage evidence when procedure has no evidence', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  app.run(`
    const result = report(fixture);
    result.checks = ['procedure','coverage'].map(dimension => ({
      id:dimension + ':first', dimension, summary:'Requirement', status:'ENTAILED', proof:{status:'ENTAILED'},
      sourceReferences:dimension === 'procedure' ? [] : [{documentId:'rationale',start:0,end:5}],
    }));
    state.activeStudy.cachedRun = result;
    renderAuditWorkspace(state.activeStudy);
  `);
  const card = app.nodes.get('finding-list').querySelector('.audit-check');
  card.listeners.click({target:card});
  assert.equal(app.run('state.activeStudy.activeDocument'), 'rationale');
  assert.deepEqual(JSON.parse(app.run('JSON.stringify(state.pinnedFindingIds)')), ['procedure:first', 'coverage:first']);
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
  app.run(`let resolveAudit; api=()=>new Promise(resolve=>{resolveAudit=resolve}); let pending=analyzeActiveStudy(); let oldStudy=state.activeStudy; state.activeStudy=null; resolveAudit(report(fixture));`);
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
      let pending = analyzeActiveStudy();
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
    let pending = analyzeActiveStudy();
  `);
  await app.run('saving');
  await app.run('analyzeActiveStudy()');
  app.run('finishSave()'); await app.run('pending');
  assert.doesNotMatch(app.nodes.get('notice').textContent, /Audit complete/);
  assert.equal(app.nodes.get('analyze-button').textContent, 'Run audit');
});

test('audit rejects a response bound to a different case', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  app.run(`api = async () => ({...report(fixture), contentHash:'stale'});`);
  await app.run('analyzeActiveStudy()');
  assert.equal(app.run('state.activeStudy.cachedRun'), null);
  assert.match(app.nodes.get('notice').textContent, /Stale audit response/);
});

test('audit save failures do not announce completion', async () => {
  const app = studio(); await app.run('importAuditText(fixture)');
  app.run(`saveStudy = async () => { throw new Error('storage failure'); };`);
  await app.run('analyzeActiveStudy()');
  assert.equal(app.nodes.get('analyze-button').textContent, 'Run audit');
  assert.match(app.nodes.get('notice').textContent, /storage failure/);
  assert.doesNotMatch(app.nodes.get('notice').textContent, /Audit complete/);
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

async function runningStudio(workflow) {
  const app = studio();
  if (workflow === 'audit_judge') await app.run('importAuditText(fixture)');
  else app.run(`
    elements.thinkingBudgetInput.value = '128';
    state.activeStudy = {id:'specification', specificationKind:'regex', specificationName:'a.yaml',
      specificationText:'rules: []', settings:{workflow:${JSON.stringify(workflow)}, thinkingBudget:128},
      cachedRun:null, incidents:{name:'cases.json',text:'[]'}};
  `);
  app.run(`
    let renders = 0;
    renderActiveStudy = () => { renders++; };
    const result = ${workflow === 'audit_judge' ? 'report(fixture)' : '{findings:[]}'};
  `);
  return app;
}

for (const workflow of ['scan', 'diagnose', 'audit_judge']) {
  test(`${workflow} ignores a response after cancellation even if fetch resolves`, async () => {
    const app = await runningStudio(workflow);
    app.run(`
      let finishRequest;
      api = () => new Promise(resolve => { finishRequest = resolve; });
      let pending = analyzeActiveStudy();
    `);
    await app.run('analyzeActiveStudy()');
    app.run('finishRequest(result)'); await app.run('pending');
    assert.equal(app.run('state.activeStudy.cachedRun'), null);
    assert.equal(app.run('renders'), 0);
    assert.equal(app.run('state.scanController'), null);
  });

  test(`${workflow} rejects same-ID replacement objects`, async () => {
    const app = await runningStudio(workflow);
    app.run(`
      let finishRequest;
      api = () => new Promise(resolve => { finishRequest = resolve; });
      const original = state.activeStudy;
      let pending = analyzeActiveStudy();
      state.activeStudy = {...original};
      finishRequest(result);
    `);
    await app.run('pending');
    assert.equal(app.run('original.cachedRun'), null);
    assert.equal(app.run('renders'), 0);
  });

  test(`${workflow} ignores changed input while a request is pending`, async () => {
    const app = await runningStudio(workflow);
    app.run(`
      let finishRequest;
      api = () => new Promise(resolve => { finishRequest = resolve; });
      let pending = analyzeActiveStudy();
      ${workflow === 'audit_judge' ? "state.activeStudy.auditCaseText += ' ';" : "state.activeStudy.specificationText += '\\n';"}
      finishRequest(result);
    `);
    await app.run('pending');
    assert.equal(app.run('state.activeStudy.cachedRun'), null);
    assert.equal(app.run('renders'), 0);
  });

  test(`${workflow} preserves newer navigation after a delayed save`, async () => {
    const app = await runningStudio(workflow);
    app.run(`
      let finishSave, markSaving;
      let saving = new Promise(resolve => { markSaving = resolve; });
      api = async () => result;
      saveStudy = () => new Promise(resolve => { finishSave = resolve; markSaving(); });
      let pending = analyzeActiveStudy();
    `);
    await app.run('saving');
    app.run(`state.activeStudy = null; elements.analyzeButton.textContent = 'Other workflow'; setNotice('New navigation'); finishSave();`);
    await app.run('pending');
    assert.equal(app.run('renders'), 0);
    assert.equal(app.nodes.get('analyze-button').textContent, 'Other workflow');
    assert.equal(app.nodes.get('notice').textContent, 'New navigation');
  });

  test(`${workflow} does not publish errors from a superseded request`, async () => {
    const app = await runningStudio(workflow);
    app.run(`
      let failRequest;
      api = () => new Promise((resolve, reject) => { failRequest = reject; });
      let pending = analyzeActiveStudy();
      state.activeStudy = null;
      setNotice('New navigation');
      failRequest(new Error('old failure'));
    `);
    await app.run('pending');
    assert.equal(app.nodes.get('notice').textContent, 'New navigation');
  });
}

for (const [field, value] of [['schemaVersion', 2], ['auditorVersion', 'old'], ['contentHash', 'old'], ['workflow', 'scan']]) {
  test(`shared cache selection rejects mismatched audit ${field}`, async () => {
    const app = studio(); await app.run('importAuditText(fixture)');
    app.run(`state.activeStudy.cachedRun = {...report(fixture), ${field}:${JSON.stringify(value)}}`);
    assert.equal(app.run('matchingCachedRun(state.activeStudy)'), null);
  });
}

test('shared cache selection retains legacy scans but not diagnosis results', () => {
  const app = studio();
  assert.equal(app.run(`matchingCachedRun({settings:{workflow:'scan'},cachedRun:{id:'old'}}).id`), 'old');
  assert.equal(app.run(`matchingCachedRun({settings:{workflow:'diagnose'},cachedRun:{id:'old'}})`), null);
});

for (const mutation of [
  'state.activeStudy.settings.provider = "other"',
  'state.activeStudy.settings.model = "other"',
  'state.activeStudy.settings.workflow = "diagnose"',
  'state.activeStudy.taxonomy = {name:"new.yaml",text:"[]"}',
  'elements.thinkingBudgetInput.value = "256"',
]) {
  test(`scan rejects pending results after ${mutation}`, async () => {
    const app = await runningStudio('scan');
    app.run(`
      let finishRequest;
      api = () => new Promise(resolve => { finishRequest = resolve; });
      let pending = analyzeActiveStudy();
      ${mutation};
      finishRequest(result);
    `);
    await app.run('pending');
    assert.equal(app.run('state.activeStudy.cachedRun'), null);
    assert.equal(app.run('renders'), 0);
  });
}

test('completed analysis preserves a visible warning when persistence fails', async () => {
  const app = await runningStudio('scan');
  app.run(`
    api = async () => result;
    storageRequest = async () => { throw new Error('storage failed'); };
  `);
  await app.run('analyzeActiveStudy()');
  assert.equal(app.run('state.storageAvailable'), false);
  assert.match(app.nodes.get('notice').textContent, /Session only: local persistence is unavailable/);
});

test('diagnosis does not depend on the scan thinking budget', async () => {
  const app = await runningStudio('diagnose');
  app.run(`
    elements.thinkingBudgetInput.value = '';
    api = async (route, options) => {
      calls.push({route, payload:JSON.parse(options.body)});
      return result;
    };
  `);
  await app.run('analyzeActiveStudy()');
  assert.equal(app.calls.at(-1).route, '/api/v1/diagnoses');
  assert.deepEqual(Object.keys(app.calls.at(-1).payload).sort(), ['specification', 'violations']);
  assert.equal(app.run('renders'), 1);
  assert.match(app.nodes.get('notice').textContent, /Diagnosis complete/);
});

test('diagnosis retains pending results when only scan settings change', async () => {
  const app = await runningStudio('diagnose');
  app.run(`
    let finishRequest;
    api = () => new Promise(resolve => { finishRequest = resolve; });
    let pending = analyzeActiveStudy();
    state.activeStudy.settings.provider = 'other';
    state.activeStudy.settings.model = 'other';
    state.activeStudy.taxonomy = {name:'scan-only.yaml',text:'[]'};
    elements.thinkingBudgetInput.value = '';
    finishRequest(result);
  `);
  await app.run('pending');
  assert.equal(app.run('state.activeStudy.cachedRun === result'), true);
  assert.equal(app.run('renders'), 1);
});

test('diagnosis rejects pending results when incidents change', async () => {
  const app = await runningStudio('diagnose');
  app.run(`
    let finishRequest;
    api = () => new Promise(resolve => { finishRequest = resolve; });
    let pending = analyzeActiveStudy();
    state.activeStudy.incidents.text = '[{"content":"new"}]';
    finishRequest(result);
  `);
  await app.run('pending');
  assert.equal(app.run('state.activeStudy.cachedRun'), null);
  assert.equal(app.run('renders'), 0);
});

test('audit execution explains when case validation is missing', async () => {
  const app = await runningStudio('audit_judge');
  app.run(`state.activeStudy.validation = null; calls.length = 0;`);
  await app.run('analyzeActiveStudy()');
  assert.equal(app.calls.length, 0);
  assert.equal(app.run('state.scanController'), null);
  assert.match(app.nodes.get('notice').textContent, /Validate this audit case before running it/);
});

test('shared card activation supports keyboard without intercepting nested controls', () => {
  const app = studio();
  app.run(`
    const card = document.createElement('article');
    let activated = 0, prevented = false;
    bindFindingCard(card, 'check', () => { activated++; });
    card.listeners.click({target:{closest:() => ({})}});
    card.listeners.keydown({target:{}, key:'Enter', preventDefault(){}});
    card.listeners.keydown({target:card, key:'Enter', preventDefault(){prevented = true;}});
  `);
  assert.equal(app.run('activated'), 1);
  assert.equal(app.run('prevented'), true);
  assert.equal(app.run('card.dataset.findingId'), 'check');
});
