const test = require('node:test');
const assert = require('node:assert/strict');
const {studio} = require('./harness.cjs');

function pickers() {
  const app = studio();
  app.run(`
    var refreshed = [];
    Object.assign(elements, {
      providerMenu: element('provider-menu'), providerPickerButton: element('provider-picker-button'),
      providerValue: element('provider-value'), modelMenu: element('model-menu'),
      modelPickerButton: element('model-picker-button'), modelValue: element('model-value'),
      modelColor: element('model-color'),
    });
    state.capabilities.providers = [
      {id: 'gemini', defaultModel: 'g1', models: ['g1', 'g2'], configured: true},
      {id: 'openai', defaultModel: 'o1', models: ['o1'], configured: false},
    ];
    state.activeStudy = {settings: {provider: 'gemini', model: 'g1'}};
    refreshAfterScanSettingsChange = () => { refreshed.push(true); };
    renderControls = () => {};
    elements.providerMenu.hidden = true;
    elements.modelMenu.hidden = true;
  `);
  app.run(`
    renderProviderPicker(state.capabilities.providers[0], true);
    renderModelPicker(state.capabilities.providers[0], 'g1', true);
  `);
  return app;
}

test('toggling opens a menu, exposes its state, and focuses the selected option', () => {
  const app = pickers();
  app.run('providerPicker.toggle()');

  assert.equal(app.nodes.get('provider-menu').hidden, false);
  assert.equal(app.nodes.get('provider-picker-button').attributes['aria-expanded'], 'true');
  const selected = app.nodes.get('provider-menu').children.find(option => option.attributes['aria-selected'] === 'true');
  assert.equal(selected.focused, true);
});

test('opening one picker closes the other', () => {
  const app = pickers();
  app.run('providerPicker.toggle()');
  app.run('modelPicker.toggle()');

  assert.equal(app.nodes.get('provider-menu').hidden, true);
  assert.equal(app.nodes.get('provider-picker-button').attributes['aria-expanded'], 'false');
  assert.equal(app.nodes.get('model-menu').hidden, false);
});

test('toggling twice closes the menu', () => {
  const app = pickers();
  app.run('modelPicker.toggle()');
  app.run('modelPicker.toggle()');

  assert.equal(app.nodes.get('model-menu').hidden, true);
  assert.equal(app.nodes.get('model-picker-button').attributes['aria-expanded'], 'false');
});

test('a disabled picker cannot be opened', () => {
  const app = pickers();
  app.nodes.get('provider-picker-button').disabled = true;
  app.run('providerPicker.toggle()');

  assert.equal(app.nodes.get('provider-menu').hidden, true);
});

test('closing restores focus only on request and only when the menu was open', () => {
  const app = pickers();
  const button = app.nodes.get('model-picker-button');
  app.run('modelPicker.close({restoreFocus: true})');
  assert.equal(button.focused, undefined);

  app.run('modelPicker.toggle()');
  app.run('modelPicker.close()');
  assert.equal(button.focused, undefined);

  app.run('modelPicker.toggle()');
  app.run('modelPicker.close({restoreFocus: true})');
  assert.equal(button.focused, true);
});

test('disabling a picker while it is open closes it', () => {
  const app = pickers();
  app.run('providerPicker.toggle()');
  app.run('renderProviderPicker(state.capabilities.providers[0], false)');

  assert.equal(app.nodes.get('provider-menu').hidden, true);
  assert.equal(app.nodes.get('provider-picker-button').disabled, true);
});

test('provider options mark the selection and flag unconfigured providers', () => {
  const app = pickers();
  const [gemini, openai] = app.nodes.get('provider-menu').children;

  assert.equal(gemini.attributes['aria-selected'], 'true');
  assert.equal(gemini.attributes.role, 'option');
  assert.equal(openai.attributes['aria-selected'], 'false');
  assert.match(openai.textContent, /not configured/);
  assert.doesNotMatch(gemini.textContent, /not configured/);
});

test('choosing another provider resets the model, closes the menu, and refreshes results', () => {
  const app = pickers();
  app.run('providerPicker.toggle()');
  app.nodes.get('provider-menu').children[1].click();

  assert.equal(app.run('state.activeStudy.settings.provider'), 'openai');
  assert.equal(app.run('state.activeStudy.settings.model'), 'o1');
  assert.equal(app.nodes.get('provider-menu').hidden, true);
  assert.equal(app.run('refreshed.length'), 1);
});

test('choosing the current provider only closes the menu', () => {
  const app = pickers();
  app.run('providerPicker.toggle()');
  app.nodes.get('provider-menu').children[0].click();

  assert.equal(app.nodes.get('provider-menu').hidden, true);
  assert.equal(app.run('refreshed.length'), 0);
});

test('choosing a different model refreshes results; choosing the same model does not', () => {
  const app = pickers();
  app.run('modelPicker.toggle()');
  app.nodes.get('model-menu').children[0].click();
  assert.equal(app.run('refreshed.length'), 0);

  app.run('modelPicker.toggle()');
  app.nodes.get('model-menu').children[1].click();
  assert.equal(app.run('state.activeStudy.settings.model'), 'g2');
  assert.equal(app.run('refreshed.length'), 1);
  assert.equal(app.nodes.get('model-menu').hidden, true);
});
