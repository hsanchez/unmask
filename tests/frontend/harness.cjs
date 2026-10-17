const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const fixture = fs.readFileSync(path.join(root, 'tests/fixtures/judge_audit/A_correct_procedurally_adherent.json'), 'utf8');

// Load scripts in the same order the page does, so a new script cannot be forgotten here.
const scriptNames = [...fs.readFileSync(path.join(root, 'src/unmask/web/index.html'), 'utf8')
  .matchAll(/<script src="\/assets\/([^"]+)"/g)].map(match => match[1]);
if (!scriptNames.length) throw new Error('no <script> tags found in index.html');

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
    const attribute = selector.match(/^\[([\w-]+)="([^"]*)"\]$/);
    const matches = attribute
      ? child => child.attributes[attribute[1]] === attribute[2]
      : child => selector.split('.').filter(Boolean).every(name => child.className.split(' ').includes(name));
    return this.children.flatMap(child => [child, ...child.querySelectorAll(selector)]).filter(matches);
  }
  focus() { this.focused = true; }
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
  for (const name of scriptNames) vm.runInContext(fs.readFileSync(path.join(root, 'src/unmask/web/assets', name), 'utf8'), context);
  vm.runInContext(`
    bindElements();
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

module.exports = {fixture, studio};
