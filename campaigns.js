/* Queue campaign setup and resumable batch progress. Credentials stay in memory.
 *
 * This file assumes index.html has already defined the globals it relies on:
 * $, escapeHtml, splitState, and renderSegments.
 */
(() => {
  'use strict';

  const STORAGE_KEY = 'xcally-campaign-batch';
  const POLL_INTERVAL_MS = 1500;
  const FINISHED_STATUSES = new Set(['completed', 'partial_failure', 'failed', 'interrupted']);

  // All mutable UI state lives here so every function below reads/writes one place.
  const state = {
    agents: [], campaigns: [], assignments: new Map(), // csv agent name -> xCALLY agent id
    busy: false, loading: false, optionsVersion: 0,
    batchId: null, batchStatus: null, missingBatch: false, submittedPreview: null,
    pollTimer: null, polling: false,
  };

  // ---------------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------------

  const normalize = value => String(value || '').normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase();
  const errorText = (data, fallback) => typeof data?.detail === 'string' ? data.detail : fallback;

  function rememberBatchId(id) {
    try { id ? sessionStorage.setItem(STORAGE_KEY, id) : sessionStorage.removeItem(STORAGE_KEY); } catch {}
  }

  function buildCredentialedForm(form) {
    const ownUsername = $('campaign-username').value.trim();
    const ownPassword = $('campaign-password').value;
    const username = ownUsername || (ownPassword ? '' : $('username').value.trim());
    const password = ownPassword || (ownUsername ? '' : $('password').value);
    if (username || password) {
      if (!username || !password) throw new Error('Enter both the xCALLY username and password.');
      form.append('username', username);
      form.append('password', password);
    }
    return form;
  }

  // ---------------------------------------------------------------------
  // Agent <-> campaign matching
  // ---------------------------------------------------------------------

  function matchingCampaigns(agentId) {
    const agent = state.agents.find(a => a.id === agentId);
    const market = $('split-market').value;
    if (!agent?.name || !market) return [];
    const expectedName = `${market}_AGENT_QUEUE_${agent.name}`.toLowerCase();
    return state.campaigns.filter(c => c.type === 'outbound' && c.name.toLowerCase() === expectedName);
  }

  function describeCampaignMatch(agentId) {
    const matches = matchingCampaigns(agentId);
    if (matches.length === 1) return matches[0].name;
    if (matches.length > 1) return 'Ambiguous campaign name: resolve duplicates in xCALLY';
    return 'No matching campaign for this market and username';
  }

  // ---------------------------------------------------------------------
  // "Upload & replace campaign lists" button + help text
  // ---------------------------------------------------------------------

  // Single source of truth for why the create button is (or isn't) disabled.
  // Checked in priority order; the first applicable reason wins.
  function disabledReason() {
    const data = splitState.data;
    const segments = data?.segments || [];
    const selectedAgentIds = segments.map(s => state.assignments.get(s.agent));

    if (state.busy) return 'A batch is being submitted or checked. Wait for its result before starting another upload.';
    if (state.loading) return 'Loading agents and campaigns…';
    if (state.batchId && !state.missingBatch) {
      return FINISHED_STATUSES.has(state.batchStatus)
        ? 'The previous batch has finished. Select another CSV or click Prepare another upload to use this file again. Review any previous failures first.'
        : 'Check the previous batch status before submitting another upload.';
    }
    if (data && data === state.submittedPreview) return 'This file preview has already been submitted. Choose a CSV again or click Prepare another upload to submit it again.';
    if (!segments.length) return 'Choose a CSV and wait for its preview.';
    if (!data.agent_column) return 'Select the column containing agent usernames or full names.';
    if (data.unassigned > 0) return `${data.unassigned} contacts have no agent. Fill in their agent values and select the corrected CSV.`;
    if (!$('split-market').value) return 'Select a market.';
    if (!state.agents.length) return 'Load agents and campaigns.';
    if (!selectedAgentIds.every(id => state.agents.some(a => a.id === id))) return 'Select an xCALLY agent for every segment.';
    if (new Set(selectedAgentIds).size !== selectedAgentIds.length) return 'Two or more segments use the same agent. Combine their names in the CSV or correct the selections.';
    if (!selectedAgentIds.every(id => matchingCampaigns(id).length === 1)) return 'Every agent must have exactly one matching campaign for the selected market.';
    return '';
  }

  function updateActions() {
    const reason = disabledReason();
    const createButton = $('campaign-create');
    createButton.disabled = Boolean(reason);
    createButton.title = reason || 'Ready to upload';
    createButton.textContent = state.busy ? 'Replacing campaign lists…' : 'Upload & replace campaign lists';
    createButton.classList.toggle('loading', state.busy);
    createButton.setAttribute('aria-busy', String(state.busy));

    $('split-creation-help').hidden = false;
    $('split-creation-help').textContent = reason || 'All agents and campaigns matched. Ready to upload and replace campaign lists.';
    $('campaign-new-upload').hidden = !FINISHED_STATUSES.has(state.batchStatus) || state.busy;
    $('campaign-load').disabled = state.busy || state.loading;
    $('split-column').disabled = state.busy || !splitState.data;

    for (const id of ['split-file', 'split-market', 'split-name', 'split-clear', 'campaign-username', 'campaign-password']) {
      $(id).disabled = state.busy;
    }
    document.querySelectorAll('[data-agent-match]').forEach(el => { el.disabled = state.busy; });
  }

  // ---------------------------------------------------------------------
  // Agent assignment table (below the CSV preview)
  // ---------------------------------------------------------------------

  function pruneStaleAssignments(segments) {
    const currentAgents = new Set(segments.map(s => s.agent));
    for (const csvAgent of state.assignments.keys()) {
      if (!currentAgents.has(csvAgent)) state.assignments.delete(csvAgent);
    }
  }

  // Auto-fill any segment that isn't assigned yet: match by xCALLY username
  // first, falling back to full name only when no username matched.
  function autoMatchAssignments(segments) {
    for (const segment of segments) {
      if (state.assignments.has(segment.agent)) continue;
      const byUsername = state.agents.filter(a => normalize(a.name) === normalize(segment.agent));
      const candidates = byUsername.length ? byUsername
        : state.agents.filter(a => normalize(a.fullname) === normalize(segment.agent));
      if (candidates.length === 1) state.assignments.set(segment.agent, candidates[0].id);
    }
  }

  function renderAgentOption(agent, selectedId) {
    const label = `${escapeHtml(agent.fullname || agent.name)} (${escapeHtml(agent.name)} · #${agent.id})`;
    return `<option value="${agent.id}" ${agent.id === selectedId ? 'selected' : ''}>${label}</option>`;
  }

  function renderAssignmentField(segment, index) {
    const selectedId = state.assignments.get(segment.agent);
    const options = state.agents.map(agent => renderAgentOption(agent, selectedId)).join('');
    return `<label class="field">
      <span>${escapeHtml(segment.agent)} · ${segment.count} contacts</span>
      <select data-agent-match="${index}"><option value="">Select an xCALLY agent</option>${options}</select>
      <p class="help">${escapeHtml(describeCampaignMatch(selectedId))}</p>
    </label>`;
  }

  function renderMatches() {
    const segments = splitState.data?.segments || [];
    if (!segments.length || !state.agents.length) {
      $('campaign-matches').innerHTML = '<p class="help">Load agents and preview a CSV to confirm each assignment.</p>';
      updateActions();
      return;
    }
    pruneStaleAssignments(segments);
    autoMatchAssignments(segments);

    const fields = segments.map(renderAssignmentField).join('');
    $('campaign-matches').innerHTML =
      '<p class="section-title">Confirm agent assignments</p>' +
      `<div class="split-fields">${fields}</div>` +
      '<p class="help">Each segment must use a different agent. Resolve unmatched or ambiguous names before replacing campaign lists.</p>';

    document.querySelectorAll('[data-agent-match]').forEach(select => select.addEventListener('change', () => {
      const segment = segments[Number(select.dataset.agentMatch)];
      state.assignments.set(segment.agent, select.value ? Number(select.value) : null);
      renderSegments();
    }));
    updateActions();
  }

  // Consumed by index.html's CSV preview code.
  window.campaignUI = {
    previewStarted() {
      if (!state.busy) state.assignments.clear();
      renderMatches();
    },
    previewChanged: renderMatches,
    targetForAgent(csvAgentName) {
      const matches = matchingCampaigns(state.assignments.get(csvAgentName));
      return matches.length === 1 ? matches[0].name : null;
    },
  };

  // ---------------------------------------------------------------------
  // Loading agents & campaigns
  // ---------------------------------------------------------------------

  function isValidOptionsResponse(data) {
    return Array.isArray(data.agents) && Array.isArray(data.campaigns)
      && data.agents.every(a => a && Number.isInteger(a.id) && typeof a.name === 'string')
      && data.campaigns.every(c => c && Number.isInteger(c.id) && typeof c.name === 'string' && typeof c.type === 'string');
  }

  async function loadCampaignOptions() {
    if (state.busy || state.loading) return;
    const version = ++state.optionsVersion;
    state.loading = true;
    state.agents = [];
    state.campaigns = [];
    state.assignments.clear();
    $('campaign-error').textContent = '';
    $('campaign-load').textContent = 'Loading…';
    renderMatches();
    try {
      const response = await fetch('/api/campaigns/options', { method: 'POST', body: buildCredentialedForm(new FormData()) });
      const data = await response.json();
      if (version !== state.optionsVersion) return;
      if (!response.ok) throw new Error(errorText(data, 'Unable to load xCALLY agents and campaigns.'));
      if (data?.workflow !== 'replace_campaign_lists_v1') {
        throw new Error('The server is running an older campaign workflow. Restart the app server, refresh this page, and load agents again.');
      }
      if (!isValidOptionsResponse(data)) throw new Error('The server returned invalid agent or campaign data. Try loading again.');
      state.agents = data.agents;
      state.campaigns = data.campaigns;
      if (!state.agents.length || !state.campaigns.length) {
        $('campaign-error').textContent = 'No accessible agents or outbound campaigns found. Check your xCALLY permissions and setup.';
      }
    } catch (error) {
      if (version !== state.optionsVersion) return;
      state.agents = [];
      state.campaigns = [];
      state.assignments.clear();
      $('campaign-error').textContent = error.message;
    } finally {
      if (version === state.optionsVersion) {
        state.loading = false;
        $('campaign-load').textContent = 'Reload agents & campaigns';
        renderSegments();
        renderMatches();
      }
    }
  }
  $('campaign-load').addEventListener('click', loadCampaignOptions);

  // Changing any credential field invalidates in-flight/loaded options so a
  // stale agent/campaign list can't be submitted under different credentials.
  function invalidateCredentials() {
    if (state.busy) return;
    ++state.optionsVersion;
    state.loading = false;
    state.agents = [];
    state.campaigns = [];
    state.assignments.clear();
    $('campaign-load').textContent = 'Load agents & campaigns';
    renderMatches();
  }
  ['campaign-username', 'campaign-password', 'username', 'password'].forEach(id => $(id).addEventListener('input', invalidateCredentials));

  // ---------------------------------------------------------------------
  // Batch result rendering
  // ---------------------------------------------------------------------

  function renderFailureSummary(failures, totalResults) {
    if (!failures.length) return '';
    const items = failures.map(r => `<div class="batch-error">
      <strong>${escapeHtml(r.agent_name || r.agent)}</strong>
      <p>Step: ${escapeHtml((r.step || 'unknown').replace(/_/g, ' '))}</p>
      <p>${escapeHtml(r.message || 'No error details were returned. Check this batch in xCALLY.')}</p>
      <p>New list: ${escapeHtml(r.list_id || 'not created')} · Campaign: ${escapeHtml(r.campaign_name || r.campaign_id || 'not matched')}</p>
    </div>`).join('');
    return `<div class="batch-errors" role="alert"><strong>${failures.length} of ${totalResults} agent updates failed</strong>${items}</div>`;
  }

  function statusBadgeClass(status) {
    if (status === 'success') return 'success';
    if (['failed', 'interrupted'].includes(status)) return 'failed';
    return 'uploading';
  }

  function renderResultRow(result) {
    const row = `<tr>
      <td>${escapeHtml(result.agent_name || result.agent)}</td>
      <td>${result.contact_count}</td>
      <td><span class="badge ${statusBadgeClass(result.status)}">${escapeHtml(result.status)}</span></td>
      <td title="${escapeHtml(result.list_name)}">${escapeHtml(result.list_id || '—')}</td>
      <td>${escapeHtml(result.campaign_name || '—')} (#${escapeHtml(result.campaign_id || '—')})</td>
      <td>${escapeHtml((result.detached_list_ids || []).join(', ') || '—')}</td>
    </tr>`;
    const stepRow = result.status === 'running'
      ? `<tr><td colspan="6">${escapeHtml((result.step || '').replace(/_/g, ' '))}</td></tr>`
      : '';
    return row + stepRow;
  }

  function renderResultsTable(results) {
    if (!results.length) return '';
    return `<div class="table-wrap"><table class="campaign-results-table">
      <thead><tr><th>Agent</th><th>Contacts</th><th>Status</th><th>New list ID</th><th>Campaign</th><th>Detached lists</th></tr></thead>
      <tbody>${results.map(renderResultRow).join('')}</tbody>
    </table></div>`;
  }

  function renderBatch(batch) {
    const results = Array.isArray(batch.results) ? batch.results : [];
    const failures = results.filter(r => ['failed', 'interrupted'].includes(r.status));
    const summary = `<div class="notice">${escapeHtml(batch.message)}<details><summary>Batch reference</summary>${escapeHtml(batch.id)}</details></div>`;
    $('campaign-results').innerHTML = renderFailureSummary(failures, results.length) + summary + renderResultsTable(results);
    if (batch.history_warning) $('campaign-error').textContent = batch.history_warning;
  }

  // ---------------------------------------------------------------------
  // Batch polling
  // ---------------------------------------------------------------------

  async function pollBatch() {
    if (!state.batchId || state.polling) return;
    clearTimeout(state.pollTimer);
    state.polling = true;
    try {
      const response = await fetch(`/api/campaign-batches/${encodeURIComponent(state.batchId)}`);
      const batch = await response.json();
      if (response.status === 404) {
        // Keep the same idempotency key when recovering an unacknowledged POST.
        state.busy = false;
        state.missingBatch = true;
        state.batchStatus = null;
        updateActions();
        $('campaign-error').textContent = 'No batch was found. You can submit the same file and agent assignments again using the retained submission ID.';
        $('campaign-refresh').hidden = false;
        return;
      }
      if (!response.ok) throw new Error(errorText(batch, 'Could not read batch status.'));
      state.missingBatch = false;
      state.batchStatus = batch.status;
      $('campaign-error').textContent = '';
      renderBatch(batch);
      state.busy = ['queued', 'running'].includes(batch.status);
      // A restored historical result is not the new CSV currently being edited.
      // Keep it in session storage for viewing, but release its submission lock.
      if (FINISHED_STATUSES.has(batch.status)) state.batchId = null;
      $('campaign-refresh').hidden = !state.busy;
      updateActions();
      if (state.busy) state.pollTimer = setTimeout(pollBatch, POLL_INTERVAL_MS);
    } catch (error) {
      $('campaign-error').textContent = `${error.message} The batch may still be running. Use Check batch status; do not resubmit.`;
      $('campaign-refresh').hidden = false;
    } finally {
      state.polling = false;
    }
  }
  $('campaign-refresh').addEventListener('click', pollBatch);

  // ---------------------------------------------------------------------
  // Batch submission
  // ---------------------------------------------------------------------

  function buildBatchForm() {
    const form = buildCredentialedForm(new FormData());
    form.append('file', $('split-file').files[0]);
    form.append('market', $('split-market').value);
    form.append('agent_column', splitState.data.agent_column);
    form.append('assignments', JSON.stringify(Object.fromEntries(state.assignments)));
    form.append('base_name', $('split-name').value.trim() || 'Contacts');
    state.batchId = state.batchId || crypto.randomUUID();
    form.append('batch_id', state.batchId);
    return form;
  }

  async function submitBatch() {
    if ($('campaign-create').disabled) return;
    $('campaign-error').textContent = '';

    let form;
    try {
      form = buildBatchForm();
    } catch (error) {
      $('campaign-error').textContent = error.message;
      return;
    }

    state.submittedPreview = splitState.data;
    rememberBatchId(state.batchId);
    state.busy = true;
    state.missingBatch = false;
    state.batchStatus = 'queued';
    updateActions();
    $('campaign-results').innerHTML = '<div class="empty">Submitting campaign batch…</div>';

    try {
      const response = await fetch('/api/campaign-batches', { method: 'POST', body: form });
      const batch = await response.json();
      if (!response.ok) {
        if ([401, 413, 422, 429].includes(response.status)) {
          state.batchId = null;
          state.batchStatus = null;
          state.submittedPreview = null;
          state.busy = false;
          rememberBatchId(null);
          updateActions();
        }
        throw new Error(errorText(batch, 'Unable to submit the list replacement batch.'));
      }
      renderBatch(batch);
      await pollBatch();
    } catch (error) {
      $('campaign-error').textContent = error.message + (state.batchId ? ' Check batch status before taking any further action.' : '');
      $('campaign-refresh').hidden = !state.batchId;
    }
  }
  $('campaign-create').addEventListener('click', submitBatch);

  // ---------------------------------------------------------------------
  // Draft lifecycle: starting over after a finished batch, or clearing entirely
  // ---------------------------------------------------------------------

  function prepareAnotherUpload() {
    if (state.busy || !FINISHED_STATUSES.has(state.batchStatus)) return;
    clearTimeout(state.pollTimer);
    state.batchId = null;
    state.batchStatus = null;
    state.submittedPreview = null;
    state.missingBatch = false;
    rememberBatchId(null);
    $('campaign-error').textContent = '';
    $('campaign-refresh').hidden = true;
    // Keep the previous result visible; creating a new batch replaces its display.
    renderMatches();
  }
  $('campaign-new-upload').addEventListener('click', prepareAnotherUpload);
  $('split-file').addEventListener('change', prepareAnotherUpload);

  function clearDraft() {
    if (state.busy) return;
    clearTimeout(state.pollTimer);
    state.batchId = null;
    state.batchStatus = null;
    state.submittedPreview = null;
    state.missingBatch = false;
    state.assignments.clear();
    rememberBatchId(null);
    $('campaign-results').innerHTML = '';
    $('campaign-error').textContent = '';
    $('campaign-refresh').hidden = true;
    renderMatches();
  }
  $('split-clear').addEventListener('click', clearDraft);

  // ---------------------------------------------------------------------
  // Startup: resume a batch left running across a page refresh, if any
  // ---------------------------------------------------------------------

  try { state.batchId = sessionStorage.getItem(STORAGE_KEY); } catch {}
  if (state.batchId) {
    state.busy = true;
    updateActions();
    pollBatch();
  } else {
    renderMatches();
  }
})();
