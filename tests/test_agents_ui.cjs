const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('agent page loads names once and reads only the selected campaign agents', async () => {
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
    const data = url.endsWith('/campaigns') ? {campaigns: [{id: 1, name: 'First'}, {id: 2, name: 'Second'}]}
      : url.endsWith('/hourly-calls/stream') ? {period_start: '2026-10-02T07:00:00+00:00', period_end: '2026-10-02T19:00:00+00:00', as_of: '2026-10-02T10:30:00+00:00', timezone: 'Africa/Accra',
        hours: [
          {start: '2026-10-02T07:00:00+00:00', end: '2026-10-02T08:00:00+00:00', complete: true, counts: {4: 3, 5: 1}},
          {start: '2026-10-02T08:00:00+00:00', end: '2026-10-02T09:00:00+00:00', complete: true, counts: {4: 2, 5: 0}},
          {start: '2026-10-02T09:00:00+00:00', end: '2026-10-02T10:00:00+00:00', complete: true, counts: {4: 1, 5: 1}},
          {start: '2026-10-02T10:00:00+00:00', end: '2026-10-02T11:00:00+00:00', complete: false, counts: {4: 1, 5: 0}},
        ], totals: {4: 7, 5: 2}, processed: 250, complete: true}
      : {id: 2, name: 'Second', agents: [
        {id: 4, name: 'Agent Available', online: true, voice_status: 'idle'},
        {id: 5, name: 'Agent Offline', online: false, voice_status: 'unknown'},
        ...Array.from({length: 39}, (_, i) => ({id: i + 6, name: `Extra Agent ${i + 1}`, online: true, voice_status: 'idle'})),
      ]};
    if (url.endsWith('/hourly-calls/stream')) {
      const chunks = [JSON.stringify({...data, processed: 0, complete: false}) + '\n', JSON.stringify(data) + '\n'];
      return {ok: true, body: {getReader() { return {
        async read() { return chunks.length ? {value: new TextEncoder().encode(chunks.shift()), done: false} : {done: true}; },
        releaseLock() {},
      }; }}};
    }
    return {ok: true, json: async () => data};
  };
  class TestDate extends Date { static now() { return Date.parse('2026-10-02T10:30:00Z'); } }
  const context = { $, fetch, FormData, Date: TestDate, Intl, TextDecoder, Uint8Array, setInterval: () => {}, window: {}, escapeHtml: value => String(value ?? ''), Number };
  vm.runInNewContext(fs.readFileSync('agents.js', 'utf8'), context);
  context.window.agentUI.opened();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, ['/api/performance/campaigns']);
  $('agents-campaign').value = '2';
  $('agents-campaign').listeners.change();
  await $('agents-check').listeners.click();
  assert.deepEqual(calls, ['/api/performance/campaigns', '/api/performance/campaigns/2/agents', '/api/performance/campaigns/2/hourly-calls/stream']);
  assert.match($('agents-detail').innerHTML, /Available/);
  assert.match($('agents-detail').innerHTML, /Today total/);
  assert.match($('agents-detail').innerHTML, /In progress/);
  assert.match($('agents-detail').innerHTML, /<strong>7<\/strong>/);
  assert.match($('agents-detail').innerHTML, /Complete · 250 call records checked/);
  assert.match($('agents-detail').innerHTML, /Showing 1–25 of 41 agents/);
  assert.match($('agents-detail').innerHTML, /Page 1 of 2/);
  assert.doesNotMatch($('agents-detail').innerHTML, /Extra Agent 39/);
  $('agents-page-next').listeners.click();
  assert.match($('agents-detail').innerHTML, /Showing 26–41 of 41 agents/);
  assert.match($('agents-detail').innerHTML, /Extra Agent 39/);
  assert.doesNotMatch($('agents-detail').innerHTML, /Agent Offline/);
  $('agents-status-filter').value = 'Offline';
  $('agents-status-filter').listeners.change({target: $('agents-status-filter')});
  assert.match($('agents-detail').innerHTML, /Showing 1–1 of 1 agents \(41 assigned\)/);
  assert.match($('agents-detail').innerHTML, /Agent Offline/);
  assert.doesNotMatch($('agents-detail').innerHTML, /Agent Available/);
  $('agents-search').value = 'available';
  $('agents-search').listeners.input({target: $('agents-search')});
  assert.match($('agents-detail').innerHTML, /Showing 0 of 41 agents/);
  $('agents-status-filter').value = '';
  $('agents-status-filter').listeners.change({target: $('agents-status-filter')});
  assert.match($('agents-detail').innerHTML, /Agent Available/);
  assert.doesNotMatch($('agents-detail').innerHTML, /Agent Offline/);
  $('agents-search').value = '44';
  $('agents-search').listeners.input({target: $('agents-search')});
  assert.match($('agents-detail').innerHTML, /Extra Agent 39/);
  assert.match($('agents-detail').innerHTML, /Showing 1–1 of 1 agents/);
  assert.equal(calls.length, 3);
  await $('agents-check').listeners.click();
  assert.deepEqual(calls.slice(3), ['/api/performance/campaigns/2/agents', '/api/performance/campaigns/2/hourly-calls/stream']);
});
