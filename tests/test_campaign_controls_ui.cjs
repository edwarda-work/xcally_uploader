const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page(responses, confirm = true, legacyCampaignUI = false) {
  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) elements.set(id, {value: '', disabled: false, innerHTML: '', textContent: '', listeners: {}, addEventListener(event, fn) {this.listeners[event] = fn;}});
    return elements.get(id);
  };
  const calls = [];
  $('control-search').value = 'Campaign';
  let related = [];
  const window = {confirm: () => confirm, campaignUI: {relatedCampaignIds: () => related}};
  if (legacyCampaignUI) window.campaignUI = {};
  vm.runInNewContext(fs.readFileSync('campaign-controls.js', 'utf8'), {
    $, window, escapeHtml: value => String(value || ''), FormData,
    fetch: async (url, options) => {
      calls.push({url, body: options.body});
      const data = responses.shift();
      if (data instanceof Error) throw data;
      return {ok: true, json: async () => data};
    },
  });
  return {$, calls, load: () => $('control-load').listeners.click(),
    search: query => { $('control-search').value = query; $('control-search').listeners.input(); },
    related: ids => { related = ids; $('control-scope').value = 'agents'; $('control-scope').listeners.change(); },
    refreshRelated: ids => { related = ids; window.campaignControls.refresh(); },
    selectAll: () => $('control-campaigns').listeners.change({target: {checked: true, hasAttribute: () => true}}),
    toggle: active => $(active ? 'control-on' : 'control-off').listeners.click(),
    row: (id, active) => $('control-campaigns').listeners.click({target: {closest: () => ({dataset: {controlId: String(id), controlActive: String(active)}})}}),
  };
}
const options = () => ({campaigns: [1,2].map(id => ({id, name: `Campaign ${id}`, type: 'outbound', dialActive: false}))});

test('older cached campaign script does not crash search, loading, or agent filtering', async () => {
  const ui = page([options()], true, true);
  await ui.load();
  ui.search('Campaign 1');
  assert.match(ui.$('control-campaigns').innerHTML, /Campaign 1/);
  ui.related([1]);
  assert.doesNotMatch(ui.$('control-campaigns').innerHTML, /data-control-id/);
  assert.equal(ui.$('control-on').disabled, true);
  ui.$('campaign-password').listeners.input();
  assert.match(ui.$('control-message').textContent, /Credentials changed/);
});

test('search hides all campaigns until a query and select all affects matching rows only', async () => {
  const ui = page([options(), {results: [{id: 2, ok: true, active: true}]}]);
  ui.search(''); await ui.load();
  assert.doesNotMatch(ui.$('control-campaigns').innerHTML, /data-control-id/);
  ui.search('CAMPAIGN 2'); ui.selectAll(); await ui.toggle(true);
  assert.equal(ui.calls[1].body.get('campaign_ids'), '[2]');
});
test('changing filters clears hidden selections and related agents track current CSV assignments', async () => {
  const ui = page([options()]);
  await ui.load(); ui.selectAll(); ui.search('Campaign 1');
  assert.match(ui.$('control-count').textContent, /1 selected/);
  ui.search(''); ui.related([2]);
  assert.match(ui.$('control-campaigns').innerHTML, /Campaign 2/);
  assert.doesNotMatch(ui.$('control-campaigns').innerHTML, /Campaign 1/);
  ui.selectAll(); ui.refreshRelated([1]);
  assert.equal(ui.$('control-on').disabled, true);
  assert.match(ui.$('control-campaigns').innerHTML, /Campaign 1/);
});

test('bulk selection sends only selected IDs and reports partial results', async () => {
  const ui = page([options(), {results: [{id: 1, ok: true, active: true}, {id: 2, ok: false, active: null, error: 'Denied'}]}]);
  await ui.load(); ui.selectAll(); await ui.toggle(true);
  assert.equal(ui.calls[1].body.get('campaign_ids'), '[1,2]');
  assert.equal(ui.calls[1].body.get('active'), 'true');
  assert.match(ui.$('control-message').textContent, /1 of 2/);
  assert.match(ui.$('control-campaigns').innerHTML, /Denied/);
  assert.match(ui.$('control-campaigns').innerHTML, /Unknown/);
});
test('individual OFF sends only that row and cancellation makes no write', async () => {
  const ui = page([options(), {results: [{id: 2, ok: true, active: false}]}]);
  await ui.load(); await ui.row(2, false);
  assert.equal(ui.calls[1].body.get('campaign_ids'), '[2]');
  assert.equal(ui.calls[1].body.get('active'), 'false');
  const cancelled = page([options()], false);
  await cancelled.load(); await cancelled.row(1, true);
  assert.equal(cancelled.calls.length, 1);
});
test('lost response marks affected statuses unknown and credentials invalidate loaded campaigns', async () => {
  const ui = page([options(), new Error('Connection lost')]);
  await ui.load(); ui.selectAll(); await ui.toggle(true);
  assert.match(ui.$('control-message').textContent, /some changes may have completed/);
  assert.match(ui.$('control-campaigns').innerHTML, /Status unknown/);
  ui.$('campaign-password').listeners.input();
  assert.equal(ui.$('control-on').disabled, true);
  assert.match(ui.$('control-campaigns').innerHTML, /No campaigns loaded/);
});
