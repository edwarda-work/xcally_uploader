/* Read-only campaign checks. Credentials and snapshots stay in memory. */
(() => {
  'use strict';
  const state = { campaigns: [], listAttempted: false, listBusy: false, detailBusy: false, checkedId: null, generation: 0 };
  const errorText = (data, fallback) => typeof data?.detail === 'string' ? data.detail : fallback;
  const cell = value => escapeHtml(value == null ? '—' : value);

  function credentials() {
    const ownUser = $('performance-username').value.trim();
    const ownPass = $('performance-password').value;
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
    if (!response.ok) throw new Error(errorText(data, 'Unable to load campaign data.'));
    return data;
  }

  function selectedId() {
    const id = Number($('performance-campaign').value);
    return Number.isInteger(id) && id > 0 && state.campaigns.some(c => c.id === id) ? id : null;
  }

  function renderSelector() {
    const current = selectedId();
    $('performance-campaign').innerHTML = '<option value="">Select a campaign</option>' +
      state.campaigns.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
    if (current) $('performance-campaign').value = String(current);
    $('performance-check').disabled = !selectedId() || state.detailBusy;
    $('performance-check').textContent = state.checkedId === selectedId() ? 'Refresh campaign' : 'Check campaign';
  }

  function renderDetail(campaign) {
    const realtime = campaign.realtime || {};
    const calls = campaign.recent_calls || [];
    const callRows = calls.map(call => `<tr><td>${cell(call.status)}</td><td>${cell(formatTime(call.started_at))}</td><td>${cell(formatTime(call.ended_at))}</td></tr>`).join('');
    $('performance-detail').innerHTML = `<div class="card"><h2>${escapeHtml(campaign.name)}</h2>
      ${campaign.agents_error ? `<div class="notice">${escapeHtml(campaign.agents_error)}</div>` : ''}
      <div class="stats"><div class="stat"><div class="stat-label">Dialing</div><div class="stat-value">${cell(campaign.active === true ? 'Active' : campaign.active === false ? 'Paused' : null)}</div></div>
      <div class="stat"><div class="stat-label">Agents available</div><div class="stat-value">${cell(realtime.available)}</div></div>
      <div class="stat"><div class="stat-label">Agents ringing</div><div class="stat-value">${cell(realtime.ringing)}</div></div></div>
      <div class="stats"><div class="stat"><div class="stat-label">Agents logged in</div><div class="stat-value">${cell(realtime.logged_in)}</div></div>
      <div class="stat"><div class="stat-label">Agents talking</div><div class="stat-value">${cell(realtime.talking)}</div></div>
      <div class="stat"><div class="stat-label">Linked contact lists</div><div class="stat-value">${cell(campaign.list_count)}</div></div></div>
      <p><strong>Assigned agents:</strong> ${cell(campaign.agents?.length)}</p>
      <button id="performance-view-agents" class="button button-secondary">View agent status</button>
      <h3>Recent calls</h3>${campaign.calls_error ? `<p class="monitor-note">${escapeHtml(campaign.calls_error)}</p>` : callRows ? `<div class="table-wrap"><table><thead><tr><th>Status</th><th>Started</th><th>Ended</th></tr></thead><tbody>${callRows}</tbody></table></div>` : '<p class="monitor-note">No call history returned for this campaign.</p>'}
      <p class="monitor-note">Agent counts are calculated from assigned agents’ current voice statuses. Recent calls show the latest 10 history entries returned by xCALLY.</p></div>`;
    $('performance-view-agents').addEventListener('click', () => {
      window.agentUI?.openCampaign(campaign.id, $('performance-username').value, $('performance-password').value);
    });
  }

  function formatTime(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  async function loadCampaigns() {
    if (state.listBusy || state.detailBusy) return;
    state.listBusy = true;
    state.listAttempted = true;
    const generation = ++state.generation;
    $('performance-refresh').disabled = true;
    $('performance-error').textContent = '';
    $('performance-updated').textContent = 'Loading campaign names…';
    try {
      const data = await post('/api/performance/campaigns');
      if (generation !== state.generation) return;
      if (!Array.isArray(data.campaigns)) throw new Error('xCALLY returned invalid campaigns.');
      state.campaigns = data.campaigns.filter(c => Number.isInteger(c.id) && c.id > 0 && typeof c.name === 'string');
      state.checkedId = null;
      renderSelector();
      $('performance-detail').innerHTML = '';
      $('performance-updated').textContent = state.campaigns.length
        ? `Loaded ${state.campaigns.length} active campaigns. Select one to check its state.`
        : 'No active outbound campaigns were found.';
    } catch (error) {
      if (generation !== state.generation) return;
      $('performance-error').textContent = error.message;
      $('performance-updated').textContent = '';
    } finally {
      state.listBusy = false;
      $('performance-refresh').disabled = false;
    }
  }

  async function checkCampaign() {
    const id = selectedId();
    if (!id || state.detailBusy || state.listBusy) return;
    state.detailBusy = true;
    const generation = state.generation;
    $('performance-check').disabled = true;
    $('performance-error').textContent = '';
    $('performance-updated').textContent = 'Checking selected campaign…';
    try {
      const campaign = await post(`/api/performance/campaigns/${id}`);
      if (generation !== state.generation || selectedId() !== id) return;
      if (campaign.id !== id) throw new Error('xCALLY returned a different campaign.');
      renderDetail(campaign);
      state.checkedId = id;
      $('performance-check').textContent = 'Refresh campaign';
      $('performance-updated').textContent = `Checked ${new Date().toLocaleString()}`;
    } catch (error) {
      if (generation !== state.generation || selectedId() !== id) return;
      $('performance-error').textContent = error.message;
      $('performance-updated').textContent = '';
    } finally {
      state.detailBusy = false;
      $('performance-check').disabled = !selectedId();
    }
  }

  $('performance-refresh').addEventListener('click', loadCampaigns);
  $('performance-check').addEventListener('click', checkCampaign);
  $('performance-campaign').addEventListener('change', () => {
    state.generation++;
    state.checkedId = null;
    $('performance-detail').innerHTML = '';
    $('performance-error').textContent = '';
    $('performance-updated').textContent = selectedId() ? 'Click Check campaign to load its current state.' : '';
    $('performance-check').disabled = !selectedId() || state.detailBusy;
    $('performance-check').textContent = 'Check campaign';
  });
  for (const id of ['performance-username', 'performance-password', 'username', 'password']) {
    $(id).addEventListener('input', () => {
      state.generation++;
      state.listAttempted = false;
      state.checkedId = null;
      state.campaigns = [];
      renderSelector();
      $('performance-detail').innerHTML = '';
      $('performance-updated').textContent = '';
    });
  }
  window.performanceUI = { opened() { if (!state.listAttempted && !state.listBusy) loadCampaigns(); } };
})();
