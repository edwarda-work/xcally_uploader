/* Read-only assigned-agent presence for one campaign. */
(() => {
  'use strict';
  const state = { campaigns: [], attempted: false, busy: false, checking: false, checkedId: null,
    agentData: null, statusFilter: '', searchQuery: '', hourlyData: null, hourlyError: '', hourlyBusy: false,
    hourlyRetryAt: 0, hourlyProgress: '', agentPage: 1, generation: 0 };
  const AGENTS_PER_PAGE = 25;
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

  async function postHourlyStream(url, onChunk) {
    const response = await fetch(url, { method: 'POST', body: credentials() });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(errorText(data, 'Unable to load hourly calls.'));
    }
    if (!response.body?.getReader) throw new Error('The browser cannot read hourly call updates.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = '';
    let completed = false;
    try {
      while (true) {
        const { value, done } = await reader.read();
        pending += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = pending.split('\n');
        pending = lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          const chunk = JSON.parse(line);
          if (chunk.error) throw new Error(chunk.error);
          onChunk(chunk);
          completed = chunk.complete === true;
        }
        if (done) break;
      }
      if (pending.trim()) {
        const chunk = JSON.parse(pending);
        if (chunk.error) throw new Error(chunk.error);
        onChunk(chunk);
        completed = chunk.complete === true;
      }
      if (!completed) throw new Error('Hourly call scan ended before it completed.');
    } finally {
      reader.releaseLock();
    }
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
      state.searchQuery = '';
      $('agents-search').value = '';
      $('agents-search').disabled = true;
      state.agentPage = 1;
      state.hourlyData = null;
      state.hourlyError = '';
      state.hourlyProgress = '';
      state.hourlyRetryAt = 0;
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
    const query = state.searchQuery.trim().toLocaleLowerCase();
    const visible = agents.filter(agent => (!state.statusFilter || status(agent) === state.statusFilter) &&
      (!query || String(agent.name).toLocaleLowerCase().includes(query) || String(agent.id).includes(query)));
    const totalPages = Math.max(1, Math.ceil(visible.length / AGENTS_PER_PAGE));
    state.agentPage = Math.min(state.agentPage, totalPages);
    const pageStart = (state.agentPage - 1) * AGENTS_PER_PAGE;
    const pageAgents = visible.slice(pageStart, pageStart + AGENTS_PER_PAGE);
    const hours = state.hourlyData?.hours || [];
    const hourHeaders = hours.map(hour => `<th>${escapeHtml(hourRange(hour))}${hour.complete ? '' : ' · In progress'}</th>`).join('');
    const rows = pageAgents.map(agent => `<tr><td>${escapeHtml(agent.name)}</td><td>${escapeHtml(status(agent))}</td>${hours.map(hour => `<td>${escapeHtml(hour.counts?.[agent.id] ?? '—')}</td>`).join('')}<td><strong>${state.hourlyData ? escapeHtml(state.hourlyData.totals?.[agent.id] ?? '—') : '—'}</strong></td></tr>`).join('');
    const dayLabel = state.hourlyData ? formatDay(state.hourlyData.period_start) : 'Today';
    $('agents-detail').innerHTML = `<div class="card"><h2>${escapeHtml(data.name)}</h2>
      ${data.error ? `<div class="notice">${escapeHtml(data.error)}</div>` : ''}
      <p class="monitor-note">Current voice status for this campaign’s assigned agents.</p>
      <p class="monitor-note"><strong>Answered calls handled:</strong> ${escapeHtml(dayLabel)}, 07:00–19:00 Africa/Accra. The current hour is partial; press Refresh agents for its latest count. New hour columns appear at each hour boundary while this tab is open.</p>
      ${state.hourlyError ? `<div class="notice">${escapeHtml(state.hourlyError)}</div>` : ''}
      ${state.hourlyProgress ? `<p class="monitor-note">${escapeHtml(state.hourlyProgress)}</p>` : ''}
      ${agents.length ? `<label class="field"><span>Filter by status</span><select id="agents-status-filter"><option value="">All statuses</option>${statuses.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('')}</select></label>
      <p class="monitor-note">${visible.length ? `Showing ${pageStart + 1}–${pageStart + pageAgents.length} of ${visible.length} agents` : `Showing 0 of ${agents.length} agents`}${state.statusFilter || query ? ` (${agents.length} assigned)` : ''}</p>
      ${rows ? `<div class="table-wrap"><table><thead><tr><th>Agent</th><th>Voice status</th>${hourHeaders}<th>Today total</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="monitor-note">No agents match the search and status filter.</p>'}` : '<p class="monitor-note">No agents are assigned to this campaign.</p>'}
      ${totalPages > 1 ? `<div class="agent-pages"><button type="button" class="button button-secondary" id="agents-page-prev" ${state.agentPage === 1 ? 'disabled' : ''}>Previous</button><span>Page ${state.agentPage} of ${totalPages}</span><button type="button" class="button button-secondary" id="agents-page-next" ${state.agentPage === totalPages ? 'disabled' : ''}>Next</button></div>` : ''}
      <p class="monitor-note">Only calls answered by an agent can be attributed from Hopper History. Unanswered dial attempts are excluded.</p></div>`;
    if (agents.length) {
      $('agents-status-filter').value = state.statusFilter;
      $('agents-status-filter').addEventListener('change', event => {
        state.statusFilter = event.target.value;
        state.agentPage = 1;
        renderDetail(state.agentData);
      });
      if (totalPages > 1) {
        $('agents-page-prev').addEventListener('click', () => { state.agentPage--; renderDetail(state.agentData); });
        $('agents-page-next').addEventListener('click', () => { state.agentPage++; renderDetail(state.agentData); });
      }
    }
  }

  function formatHour(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Africa/Accra', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(date);
  }

  function formatDay(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Africa/Accra', day: '2-digit', month: 'short', year: 'numeric',
    }).format(date);
  }

  function hourRange(hour) { return `${formatHour(hour.start)}–${formatHour(hour.end)}`; }

  async function loadHourlyCalls(id, force = false) {
    if (state.hourlyBusy || !state.agentData || selectedId() !== id) return;
    if (!force && Date.now() < state.hourlyRetryAt) return;
    state.hourlyBusy = true;
    const generation = state.generation;
    try {
      await postHourlyStream(`/api/performance/campaigns/${id}/hourly-calls/stream`, data => {
        if (generation !== state.generation || selectedId() !== id) return;
        if (!data || !Array.isArray(data.hours) || !data.totals || Array.isArray(data.totals) ||
            !Number.isFinite(Date.parse(data.period_start)) || !Number.isFinite(Date.parse(data.period_end)) ||
            !Number.isFinite(Date.parse(data.as_of)) || !Number.isInteger(data.processed)) {
          throw new Error('xCALLY returned invalid hourly call counts.');
        }
        state.hourlyData = data;
        state.hourlyError = '';
        state.hourlyProgress = data.complete ? `Complete · ${data.processed} call records checked` :
          `Loading call history… ${data.processed} records checked. Counts are partial.`;
        renderDetail(state.agentData);
      });
      if (generation !== state.generation || selectedId() !== id) return;
      const data = state.hourlyData;
      const asOf = Date.parse(data.as_of);
      const dayStart = Date.parse(data.period_start);
      const dayEnd = Date.parse(data.period_end);
      state.hourlyRetryAt = asOf < dayStart ? dayStart : asOf >= dayEnd
        ? dayStart + 86400000 : Math.floor(asOf / 3600000) * 3600000 + 3600000;
    } catch (error) {
      if (generation !== state.generation || selectedId() !== id) return;
      state.hourlyError = error.message;
      state.hourlyProgress = state.hourlyData ? 'Partial results only.' : '';
      state.hourlyRetryAt = Math.floor(Date.now() / 3600000) * 3600000 + 3600000;
    } finally {
      state.hourlyBusy = false;
      if (generation === state.generation && selectedId() === id) renderDetail(state.agentData);
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
      $('agents-search').disabled = false;
      renderDetail(data);
      state.checkedId = id;
      $('agents-check').textContent = 'Refresh agents';
      $('agents-updated').textContent = `Checked ${new Date().toLocaleString()}`;
      await loadHourlyCalls(id, Boolean(state.hourlyError || state.hourlyData?.hours?.at(-1)?.complete === false));
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
  $('agents-search').addEventListener('input', event => {
    state.searchQuery = event.target.value;
    state.agentPage = 1;
    if (state.agentData) renderDetail(state.agentData);
  });
  $('agents-campaign').addEventListener('change', () => {
    state.generation++;
    state.checkedId = null;
    state.agentData = null;
    state.statusFilter = '';
    state.searchQuery = '';
    $('agents-search').value = '';
    $('agents-search').disabled = true;
    state.agentPage = 1;
    state.hourlyData = null;
    state.hourlyError = '';
    state.hourlyProgress = '';
    state.hourlyRetryAt = 0;
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
      state.searchQuery = '';
      $('agents-search').value = '';
      $('agents-search').disabled = true;
      state.agentPage = 1;
      state.hourlyData = null;
      state.hourlyError = '';
      state.hourlyProgress = '';
      state.hourlyRetryAt = 0;
      renderSelector(null);
      $('agents-detail').innerHTML = '';
      $('agents-updated').textContent = '';
    });
  }
  window.agentUI = {
    opened() {
      if (!state.attempted && !state.busy) loadCampaigns();
      if (state.agentData && selectedId()) loadHourlyCalls(selectedId());
    },
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
  setInterval(() => {
    if ($('agents').classList.contains('active') && state.agentData && selectedId()) {
      loadHourlyCalls(selectedId());
    }
  }, 30000);
})();
