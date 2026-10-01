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
    const data = url.endsWith('/campaigns')
      ? {campaigns: [{id: 1, name: 'First'}, {id: 2, name: 'Second'}]}
      : {id: 2, name: 'Second', agents: [
        {id: 4, name: 'Agent Available', online: true, voice_status: 'idle'},
        {id: 5, name: 'Agent Offline', online: false, voice_status: 'unknown'},
      ]};
    return {ok: true, json: async () => data};
  };
  const context = { $, fetch, FormData, Date, window: {}, escapeHtml: value => String(value ?? ''), Number };
  vm.runInNewContext(fs.readFileSync('agents.js', 'utf8'), context);
  context.window.agentUI.opened();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, ['/api/performance/campaigns']);
  $('agents-campaign').value = '2';
  $('agents-campaign').listeners.change();
  await $('agents-check').listeners.click();
  assert.deepEqual(calls, ['/api/performance/campaigns', '/api/performance/campaigns/2/agents']);
  assert.match($('agents-detail').innerHTML, /Available/);
  assert.match($('agents-detail').innerHTML, /Showing 2 of 2 agents/);
  $('agents-status-filter').value = 'Offline';
  $('agents-status-filter').listeners.change({target: $('agents-status-filter')});
  assert.match($('agents-detail').innerHTML, /Showing 1 of 2 agents/);
  assert.match($('agents-detail').innerHTML, /Agent Offline/);
  assert.doesNotMatch($('agents-detail').innerHTML, /Agent Available/);
  assert.equal(calls.length, 2);
});
