const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page() {
  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', innerHTML: '', disabled: false, listeners: {},
      addEventListener(name, fn) { this.listeners[name] = fn; },
    });
    return elements.get(id);
  };
  const calls = [];
  const fetch = async url => {
    calls.push(url);
    const data = url.endsWith('/campaigns')
      ? {campaigns: [{id: 1, name: 'First'}, {id: 2, name: 'Second'}]}
      : {id: Number(url.split('/').at(-1)), name: 'First', active: true, agents: [], list_count: 1,
        realtime: {logged_in: 2, available: 1, talking: 1, ringing: 0},
        recent_calls: [{status: 'Answer', started_at: '2026-10-01T10:04:17Z', ended_at: '2026-10-01T10:05:11Z'}]};
    return {ok: true, json: async () => data};
  };
  const context = { $, fetch, FormData, Date, window: {}, escapeHtml: value => String(value ?? ''), Number };
  vm.runInNewContext(fs.readFileSync('performance.js', 'utf8'), context);
  return { $, calls, ui: context.window.performanceUI };
}

test('opening loads only names and checks only the chosen campaign', async () => {
  const p = page();
  p.ui.opened();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(p.calls, ['/api/performance/campaigns']);
  p.ui.opened();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(p.calls.length, 1);
  p.$('performance-campaign').value = '2';
  p.$('performance-campaign').listeners.change();
  await p.$('performance-check').listeners.click();
  assert.deepEqual(p.calls, ['/api/performance/campaigns', '/api/performance/campaigns/2']);
  assert.equal(p.$('performance-check').textContent, 'Refresh campaign');
  assert.match(p.$('performance-detail').innerHTML, /Answer/);
  assert.match(p.$('performance-detail').innerHTML, /Agents ringing/);
  assert.doesNotMatch(p.$('performance-detail').innerHTML, /Calls originating now/);
  await p.$('performance-check').listeners.click();
  assert.deepEqual(p.calls, ['/api/performance/campaigns', '/api/performance/campaigns/2', '/api/performance/campaigns/2']);
});
