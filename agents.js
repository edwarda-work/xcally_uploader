/* Read-only assigned-agent presence for one campaign. */
(() => {
  'use strict';
  const state = { campaigns: [], attempted: false, busy: false, checking: false, checkedId: null,
    agentData: null, statusFilter: '', generation: 0 };
  const errorText = (data, fallback) => typeof data?.detail === 'string' ? data.detail : fallback;

  function credentials() {
    const ownUser = $('agents-username').value.trim();
    const ownPass = $('agents-password').value;
    const user = ownUser || (ownPass ? '' : $('username').value.trim());
    const pass = ownPass || (ownUser ? '' : $('password').value);
    if ((user || pass) && (!user || !pass)) throw new Error('Enter both the xCALLY username and password.');
    const form = new FormData();
    if (user) { form.append('username', user); form.append('password', pass); }
    return form;
  }

  async function post(url) {
    const response = await fetch(url, { method: 'POST', body: credentials() });
    const data = await response.json().catch(() => null);
    if (!response.ok) throw new Error(errorText(data, 'Unable to load agent data.'));
    return data;
  }

  function selectedId() {
    const id = Number($('agents-campaign').value);
    return Number.isInteger(id) && id > 0 && state.campaigns.some(c => c.id === id) ? id : null;
  }

  function renderSelector(preferredId = selectedId()) {
    $('agents-campaign').innerHTML = '<option value="">Select a campaign</option>' +
      state.campaigns.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
    if (preferredId && state.campaigns.some(c => c.id === preferredId)) $('agents-campaign').value = String(preferredId);
    $('agents-check').disabled = !selectedId() || state.checking;
    $('agents-check').textContent = state.checkedId === selectedId() ? 'Refresh agents' : 'Check agents';
  }

  async function loadCampaigns(preferredId = selectedId()) {
    if (state.busy || state.checking) return;
    state.busy = true;
    state.attempted = true;
    const generation = ++state.generation;
    $('agents-load').disabled = true;
    $('agents-error').textContent = '';
    $('agents-updated').textContent = 'Loading active campaigns…';
    try {
      const data = await post('/api/performance/campaigns');
      if (generation !== state.generation) return;
      if (!Array.isArray(data.campaigns)) throw new Error('xCALLY returned invalid campaigns.');
      state.campaigns = data.campaigns.filter(c => Number.isInteger(c.id) && c.id > 0 && typeof c.name === 'string');
      state.checkedId = null;
      state.agentData = null;
      state.statusFilter = '';
      renderSelector(preferredId);
      $('agents-detail').innerHTML = '';
      $('agents-updated').textContent = state.campaigns.length
        ? 'Select a campaign to check its agents.' : 'No active outbound campaigns were found.';
    } catch (error) {
      if (generation !== state.generation) return;
      $('agents-error').textContent = error.message;
      $('agents-updated').textContent = '';
    } finally {
      state.busy = false;
      $('agents-load').disabled = false;
    }
  }

  function status(agent) {
    if (agent.online === false) return 'Offline';
    if (agent.online !== true) return 'Unknown';
    const voice = String(agent.voice_status || '').toLowerCase();
    return ({ idle: 'Available', pause: 'Paused', talking: 'Talking', ringing: 'Ringing', unavailable: 'Unavailable' })[voice]
      || (voice ? voice : 'Online');
  }

  function renderDetail(data) {
    const agents = data.agents || [];
    const statuses = [...new Set(agents.map(status))].sort((a, b) => a.localeCompare(b));
    if (state.statusFilter && !statuses.includes(state.statusFilter)) state.statusFilter = '';
    const visible = state.statusFilter ? agents.filter(agent => status(agent) === state.statusFilter) : agents;
    const rows = visible.map(agent => `<tr><td>${escapeHtml(agent.name)}</td><td>${escapeHtml(status(agent))}</td></tr>`).join('');
    $('agents-detail').innerHTML = `<div class="card"><h2>${escapeHtml(data.name)}</h2>
      ${data.error ? `<div class="notice">${escapeHtml(data.error)}</div>` : ''}
      <p class="monitor-note">Current voice status for this campaign’s assigned agents.</p>
      ${agents.length ? `<label class="field"><span>Filter by status</span><select id="agents-status-filter"><option value="">All statuses</option>${statuses.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('')}</select></label>
      <p class="monitor-note">Showing ${visible.length} of ${agents.length} agents</p>
      ${rows ? `<div class="table-wrap"><table><thead><tr><th>Agent</th><th>Voice status</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="monitor-note">No agents match this status.</p>'}` : '<p class="monitor-note">No agents are assigned to this campaign.</p>'}
      <p class="monitor-note">Call rates and rankings will need verified agent-linked call history and a selected time range.</p></div>`;
    if (agents.length) {
      $('agents-status-filter').value = state.statusFilter;
      $('agents-status-filter').addEventListener('change', event => {
        state.statusFilter = event.target.value;
        renderDetail(state.agentData);
      });
    }
  }

  async function checkAgents() {
    const id = selectedId();
    if (!id || state.busy || state.checking) return;
    state.checking = true;
    const generation = state.generation;
    $('agents-check').disabled = true;
    $('agents-error').textContent = '';
    $('agents-updated').textContent = 'Checking assigned agents…';
    try {
      const data = await post(`/api/performance/campaigns/${id}/agents`);
      if (generation !== state.generation || selectedId() !== id) return;
      if (data.id !== id || !Array.isArray(data.agents)) throw new Error('xCALLY returned invalid campaign agents.');
      state.agentData = data;
      renderDetail(data);
      state.checkedId = id;
      $('agents-check').textContent = 'Refresh agents';
      $('agents-updated').textContent = `Checked ${new Date().toLocaleString()}`;
    } catch (error) {
      if (generation !== state.generation || selectedId() !== id) return;
      $('agents-error').textContent = error.message;
      $('agents-updated').textContent = '';
    } finally {
      state.checking = false;
      $('agents-check').disabled = !selectedId();
    }
  }

  $('agents-load').addEventListener('click', () => loadCampaigns());
  $('agents-check').addEventListener('click', checkAgents);
  $('agents-campaign').addEventListener('change', () => {
    state.generation++;
    state.checkedId = null;
    state.agentData = null;
    state.statusFilter = '';
    $('agents-check').textContent = 'Check agents';
    $('agents-check').disabled = !selectedId() || state.checking;
    $('agents-detail').innerHTML = '';
    $('agents-error').textContent = '';
  });
  for (const id of ['agents-username', 'agents-password', 'username', 'password']) {
    $(id).addEventListener('input', () => {
      state.generation++;
      state.attempted = false;
      state.campaigns = [];
      state.checkedId = null;
      state.agentData = null;
      state.statusFilter = '';
      renderSelector(null);
      $('agents-detail').innerHTML = '';
      $('agents-updated').textContent = '';
    });
  }
  window.agentUI = {
    opened() { if (!state.attempted && !state.busy) loadCampaigns(); },
    async openCampaign(id, username, password) {
      let needsLoad = !state.attempted;
      if ($('agents-username').value !== username || $('agents-password').value !== password) {
        $('agents-username').value = username;
        $('agents-password').value = password;
        needsLoad = true;
      }
      state.attempted = true;
      document.querySelector('.nav-button[data-tab="agents"]').click();
      if (needsLoad) await loadCampaigns(id);
      if (state.campaigns.some(c => c.id === id)) {
        $('agents-campaign').value = String(id);
        await checkAgents();
      }
    },
  };
})();
