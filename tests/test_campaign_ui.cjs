const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page(responses, remembered = null, segments = [{agent: 'Edward Apersil', count: 5}]) {
  const elements = new Map([...fs.readFileSync('index.html', 'utf8').matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
    value: '', disabled: false, innerHTML: '', textContent: '', listeners: {},
    classList: {toggle() {}}, setAttribute() {},
    addEventListener(event, fn) { this.listeners[event] = fn; },
  }]));
  const $ = id => { assert.ok(elements.has(id), `Missing UI element ${id}`); return elements.get(id); };
  $('split-market').value = 'GH';
  const context = { $, window: {}, document: {querySelectorAll: () => []},
    splitState: {data: {segments, agent_column: 'AGENT_NAME', unassigned: 0}},
    escapeHtml: value => String(value ?? ''),
    FormData: class {append() {}}, sessionStorage: {getItem: () => remembered}, setTimeout: () => 0, clearTimeout,
    fetch: async () => ({ok: true, json: async () => responses.shift()}),
  };
  context.renderSegments = () => context.window.campaignUI.previewChanged();
  vm.runInNewContext(fs.readFileSync('campaigns.js', 'utf8'), context);
  return {$, load: () => $('campaign-load').listeners.click(),
    chooseFile: () => $('split-file').listeners.change(),
    preview: segments => {
      context.splitState.data = null; context.window.campaignUI.previewStarted();
      context.splitState.data = {segments, agent_column: 'AGENT_NAME', unassigned: 0};
      context.renderSegments();
    },
    anotherUpload: () => $('campaign-new-upload').listeners.click()};
}
const valid = {workflow: 'replace_campaign_lists_v1',
  agents: [{id: 1, name: 'edwarda', fullname: 'Edward Apersil'}],
  campaigns: [{id: 20, name: 'GH_AGENT_QUEUE_EDWARDA', type: 'outbound'}]};

test('old template response does not crash or leave reload disabled', async () => {
  const ui = page([{agents: valid.agents, templates: []}]);
  await ui.load();
  assert.match(ui.$('campaign-error').textContent, /Restart the app server/);
  assert.equal(ui.$('campaign-load').disabled, false);
  assert.equal(ui.$('campaign-create').disabled, true);
});

test('missing arrays and null entries are rejected before updating state', async () => {
  for (const response of [null, {workflow: valid.workflow}, {...valid, agents: null}, {...valid, campaigns: [null]}]) {
    const ui = page([response]);
    await ui.load();
    assert.ok(ui.$('campaign-error').textContent);
    assert.equal(ui.$('campaign-load').disabled, false);
    assert.equal(ui.$('campaign-create').disabled, true);
  }
});

test('a valid retry recovers and matches the existing campaign', async () => {
  const ui = page([{agents: []}, valid]);
  await ui.load();
  await ui.load();
  assert.equal(ui.$('campaign-error').textContent, '');
  assert.equal(ui.$('campaign-load').disabled, false);
  assert.equal(ui.$('campaign-create').disabled, false);
  assert.match(ui.$('campaign-matches').innerHTML, /GH_AGENT_QUEUE_EDWARDA/);
});

test('restored failed batches show full errors above the result table', async () => {
  const message = 'Contact count could not be verified. Existing campaign lists were not changed.';
  const ui = page([{id: 'saved-batch', status: 'partial_failure', message: 'Finished', results: [
    {agent_name: 'Edmund Sarpong', contact_count: 5, status: 'failed', list_id: 8047,
     campaign_name: 'GH_AGENT_QUEUE_EDMUNDS', campaign_id: 147, step: 'verifying_contact_import', message}
  ]}], 'saved-batch');
  await new Promise(resolve => setImmediate(resolve));
  const html = ui.$('campaign-results').innerHTML;
  assert.match(html, /role="alert"/);
  assert.ok(html.includes(message));
  assert.ok(html.indexOf(message) < html.indexOf('<table'));
  assert.match(html, /1 of 1 agent updates failed/);
});

const usernameOptions = {workflow: valid.workflow,
  agents: [
    {id: 17, name: 'christianab', fullname: 'Christiana Bassaw'},
    {id: 20, name: 'derricka', fullname: 'Derrick Amponsah'},
    {id: 14, name: 'banfordo', fullname: 'Banford Ollenu'},
    {id: 38, name: 'princea', fullname: 'Prince Asante'},
  ],
  campaigns: ['CHRISTIANAB', 'DERRICKA', 'BANFORDO', 'PRINCEA'].map((name, i) =>
    ({id: 100 + i, name: 'GH_AGENT_QUEUE_' + name, type: 'outbound'}))};
const usernameSegments = usernameOptions.agents.map(a => ({agent: a.name, count: 5}));

test('CSV usernames match all four agents and enable upload', async () => {
  const ui = page([usernameOptions], null, usernameSegments);
  await ui.load();
  assert.equal(ui.$('campaign-create').disabled, false);
  assert.match(ui.$('split-creation-help').textContent, /Ready to upload/);
  for (const campaign of usernameOptions.campaigns) {
    assert.ok(ui.$('campaign-matches').innerHTML.includes(campaign.name));
  }
});

test('a restored completed batch does not block the new draft', async () => {
  const ui = page([{id: 'previous', status: 'completed', results: []}, usernameOptions], 'previous', usernameSegments);
  await new Promise(resolve => setImmediate(resolve));
  await ui.load();
  assert.equal(ui.$('campaign-create').disabled, false);
  ui.chooseFile();
  assert.equal(ui.$('campaign-create').disabled, false);
});

test('explicit new upload allows reusing a file after a failed batch', async () => {
  const ui = page([{id: 'previous', status: 'partial_failure', results: []}, usernameOptions], 'previous', usernameSegments);
  await new Promise(resolve => setImmediate(resolve));
  await ui.load();
  assert.equal(ui.$('campaign-new-upload').hidden, false);
  ui.anotherUpload();
  assert.equal(ui.$('campaign-create').disabled, false);
  assert.equal(ui.$('campaign-new-upload').hidden, true);
});

test('choosing a file cannot clear a running batch lock', async () => {
  const ui = page([{id: 'previous', status: 'running', results: []}], 'previous', usernameSegments);
  await new Promise(resolve => setImmediate(resolve));
  ui.chooseFile();
  ui.anotherUpload();
  assert.equal(ui.$('campaign-create').disabled, true);
  assert.equal(ui.$('split-file').disabled, true);
  assert.equal(ui.$('campaign-new-upload').hidden, true);
});

test('changing username values recalculates matches without reloading options', async () => {
  const ui = page([usernameOptions], null, [{agent: 'christianab', count: 5}]);
  await ui.load();
  ui.preview([{agent: 'derricka', count: 5}, {agent: 'princea', count: 5}]);
  assert.equal(ui.$('campaign-create').disabled, false);
  assert.match(ui.$('campaign-matches').innerHTML, /GH_AGENT_QUEUE_DERRICKA/);
  assert.match(ui.$('campaign-matches').innerHTML, /GH_AGENT_QUEUE_PRINCEA/);
});

test('a username takes priority over a different agents full name', async () => {
  const options = {...usernameOptions, agents: [...usernameOptions.agents,
    {id: 99, name: 'another_user', fullname: 'christianab'}]};
  const ui = page([options], null, [{agent: 'christianab', count: 5}]);
  await ui.load();
  assert.equal(ui.$('campaign-create').disabled, false);
  assert.match(ui.$('campaign-matches').innerHTML, /GH_AGENT_QUEUE_CHRISTIANAB/);
});
