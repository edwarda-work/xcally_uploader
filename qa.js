/* Quality Assurance workspace: call sampling, evaluation, scorecards.
   Credentials stay in memory and are sent only in POST bodies. */
(() => {
  'use strict';
  const state = {
    loaded: {}, scorecards: [], plans: [], plan: null, evaluations: new Map(),
    selected: null, player: null, busy: false, markets: [],
  };
  const errorText = (data, fallback) => typeof data?.detail === 'string' ? data.detail
    : Array.isArray(data?.detail) ? data.detail.map(d => d.msg).join(' ') : fallback;
  const cell = value => escapeHtml(value == null || value === '' ? '—' : value);

  function credentials() {
    const ownUser = $('qa-username').value.trim();
    const ownPass = $('qa-password').value;
    const user = ownUser || (ownPass ? '' : $('username').value.trim());
    const pass = ownPass || (ownUser ? '' : $('password').value);
    if ((user || pass) && (!user || !pass)) throw new Error('Enter both the xCALLY username and password.');
    const form = new FormData();
    if (user) { form.append('username', user); form.append('password', pass); }
    return form;
  }

  async function request(url, options = {}, fallback = 'The request failed.') {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => null);
    if (!response.ok) throw new Error(errorText(data, fallback));
    return data;
  }
  const sendJson = (url, body) => request(url, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  }, 'Unable to save.');

  function duration(seconds) {
    if (!Number.isFinite(seconds)) return '—';
    return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, '0')}`;
  }
  function when(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }
  const isoDay = date => date.toISOString().slice(0, 10);
  // GH_QUEUE_COLLECTIONS_0_30_CAMPAIGN -> "0 30"; the full name stays in the tooltip.
  function shortCampaign(name) {
    if (!name) return '—';
    const short = String(name).replace(/^[A-Z]{2}_(QUEUE|AGENT_QUEUE)_(COLLECTIONS_)?/i, '').replace(/_CAMPAIGN$/i, '').replace(/_/g, ' ').trim();
    return short || String(name);
  }

  // ----- Scorecards -----

  function slug(label, taken) {
    const base = label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 40) || 'item';
    let key = base, n = 2;
    while (taken.has(key)) key = `${base}_${n++}`;
    taken.add(key);
    return key;
  }

  function parseItems(text) {
    const taken = new Set();
    let domain = null;
    return text.split('\n').map(line => line.trim()).filter(Boolean).flatMap((line, index) => {
      if (line.startsWith('#')) { domain = line.replace(/^#+/, '').trim() || null; return []; }
      const [label, weightText, flag] = line.split('|').map(part => (part || '').trim());
      const weight = weightText ? Number(weightText) : 1;
      if (!label) throw new Error(`Line ${index + 1} needs a label.`);
      if (!Number.isFinite(weight) || weight <= 0) throw new Error(`Line ${index + 1}: weight must be a positive number.`);
      if (flag && !['auto-fail', 'critical'].includes(flag.toLowerCase())) throw new Error(`Line ${index + 1}: the third part can only be "auto-fail".`);
      return [{key: slug(label, taken), label, weight, auto_fail: Boolean(flag), domain}];
    });
  }

  async function loadScorecards() {
    state.scorecards = (await request('/api/qa/scorecards', {}, 'Unable to load scorecards.')).scorecards;
    renderScorecards();
  }

  function renderScorecards() {
    $('qa-card-list').innerHTML = state.scorecards.length ? state.scorecards.map(card => `<div class="card">
      <div class="history-head" style="margin-bottom:10px"><div><strong>${escapeHtml(card.name)}</strong>
      <span class="count">${escapeHtml(card.market || 'All markets')} · ${card.items.length} items</span></div>
      <button class="button button-secondary" data-archive="${escapeHtml(card.id)}">Archive</button></div>
      <div class="table-wrap"><table><thead><tr><th>Metric</th><th>Weight</th><th>Graded 1 fails the call</th></tr></thead><tbody>
      ${byDomain(card.items).map(group => (group.domain ? `<tr class="qa-domain-row"><th colspan="3">${escapeHtml(group.domain)} · ${group.weight}%</th></tr>` : '') +
        group.items.map(item => `<tr><td>${escapeHtml(item.label)}</td><td>${item.weight}</td><td>${autoFail(item) ? 'Yes' : ''}</td></tr>`).join('')).join('')}
      </tbody></table></div></div>`).join('') : '<div class="empty">No scorecards yet. Create one above.</div>';
    document.querySelectorAll('[data-archive]').forEach(button => button.addEventListener('click', async () => {
      if (!confirm('Archive this scorecard? Past evaluations keep their copy of it.')) return;
      try {
        await request(`/api/qa/scorecards/${encodeURIComponent(button.dataset.archive)}`, {method: 'DELETE'}, 'Unable to archive.');
        await loadScorecards();
      } catch (error) { $('qa-card-error').textContent = error.message; }
    }));
    $('qa-eval-scorecard').innerHTML = '<option value="">Select a scorecard</option>' +
      state.scorecards.map(card => `<option value="${escapeHtml(card.id)}">${escapeHtml(card.name)}</option>`).join('');
  }

  const autoFail = item => Boolean(item.auto_fail ?? item.critical);
  // Consecutive items sharing a domain, in scorecard order: [{domain, weight, items}].
  function byDomain(items) {
    const groups = [];
    for (const item of items) {
      const last = groups[groups.length - 1];
      if (last && last.domain === (item.domain || null)) { last.items.push(item); last.weight += item.weight; }
      else groups.push({domain: item.domain || null, weight: item.weight, items: [item]});
    }
    return groups;
  }
  const domainsText = e => (e.domains || []).map(d => `${d.name} ${d.percent == null ? 'N/A' : d.percent + '%'}`).join(' · ');
  const percentText = e => e.percent == null ? 'N/A' : `${e.percent}%`;
  const resultText = e => `${percentText(e)}${e.critical_failed ? ' · critical fail' : ''}${e.overall_fails ? ` · ${e.overall_fails} below 5` : ''}`;

  async function saveScorecard() {
    $('qa-card-error').textContent = '';
    try {
      if (!$('qa-card-items').value.trim()) throw new Error('The items box is empty: the grey text is only an example. Type your items, or click Use sample items.');
      const items = parseItems($('qa-card-items').value);
      await sendJson('/api/qa/scorecards', {name: $('qa-card-name').value, market: $('qa-card-market').value || null, items});
      $('qa-card-name').value = ''; $('qa-card-items').value = '';
      await loadScorecards();
    } catch (error) { $('qa-card-error').textContent = error.message; }
  }

  // ----- Sampling -----

  function planHtml(plan) {
    const skipped = plan.skipped || {};
    const blocks = plan.assignments.map(entry => `<div class="card"><p class="section-title">${escapeHtml(entry.evaluator)}
      <span class="count">${entry.agents.length} agents</span></p>
      ${entry.agents.length ? entry.agents.map(agent => `<div class="qa-agent"><h3>${cell(agent.agent_name)} <span class="count">ID ${agent.agent_id}</span></h3>
        ${agent.missing.length || agent.short_of_target ? `<div class="notice">${agent.missing.length ? `No ${agent.missing.join(', ')} calls in this period. ` : ''}${agent.short_of_target ? `${agent.short_of_target} fewer than ${plan.thresholds?.calls_per_agent || 4} calls available.` : ''}</div>` : ''}
        <div class="table-wrap"><table><thead><tr><th>Length</th><th>Duration</th><th>Recording</th><th>Campaign</th><th>Date</th></tr></thead><tbody>
        ${agent.calls.map(call => `<tr><td><span class="bucket ${call.bucket}">${call.bucket}</span></td><td>${duration(call.duration)}</td><td>${call.recording_id}</td><td class="qa-campaign" title="${escapeHtml(call.campaign || '')}">${escapeHtml(shortCampaign(call.campaign))}</td><td>${cell(when(call.created_at))}</td></tr>`).join('')}
        </tbody></table></div></div>`).join('') : '<div class="empty">No agents assigned.</div>'}</div>`).join('');
    const skippedText = [
      skipped.already_evaluated && `${skipped.already_evaluated} already evaluated`,
      skipped.no_agent && `${skipped.no_agent} without an agent`,
      skipped.no_duration && `${skipped.no_duration} without a duration`,
      skipped.empty && `${skipped.empty} empty (0:00)`,
      skipped.other_market && `${skipped.other_market} from other markets or with no campaign`,
    ].filter(Boolean).join(', ');
    return `<div class="card"><p class="section-title">Sample ${escapeHtml(plan.period_start)} to ${escapeHtml(plan.period_end)}${plan.campaign ? ` · ${escapeHtml(plan.campaign)}` : ''}</p>
      <p class="monitor-note">Seed ${escapeHtml(plan.seed)}.${skippedText ? ` Recordings left out: ${escapeHtml(skippedText)}.` : ''}</p></div>${blocks}`;
  }

  async function loadPlans() {
    state.plans = (await request('/api/qa/plans', {}, 'Unable to load samples.')).plans;
    $('qa-plans').innerHTML = state.plans.length ? `<div class="table-wrap"><table><thead><tr><th>Created</th><th>Period</th><th>Campaign</th><th>Evaluators</th><th>Agents</th><th></th></tr></thead><tbody>
      ${state.plans.map(plan => `<tr><td>${cell(when(plan.created_at))}</td><td>${escapeHtml(plan.period_start)} – ${escapeHtml(plan.period_end)}</td><td>${cell(plan.campaign)}</td><td>${escapeHtml(plan.evaluators.join(', '))}</td><td>${plan.agents}</td><td><button class="button button-secondary" data-plan="${escapeHtml(plan.id)}">View</button></td></tr>`).join('')}
      </tbody></table></div>` : '<div class="empty">No samples yet.</div>';
    document.querySelectorAll('[data-plan]').forEach(button => button.addEventListener('click', async () => {
      try { $('qa-plan').innerHTML = planHtml(await request(`/api/qa/plans/${encodeURIComponent(button.dataset.plan)}`)); }
      catch (error) { $('qa-sampling-error').textContent = error.message; }
    }));
    $('qa-eval-plan').innerHTML = '<option value="">Select a sample</option>' + state.plans.map(plan =>
      `<option value="${escapeHtml(plan.id)}">${escapeHtml(plan.market || 'All markets')} · ${escapeHtml(plan.period_start)} – ${escapeHtml(plan.period_end)}${plan.campaign ? ` · ${escapeHtml(shortCampaign(plan.campaign))}` : ''} · made ${escapeHtml(when(plan.created_at))}</option>`).join('');
    // Newest sample first and preselected, so an older sample isn't opened by mistake.
    if (state.plans.length && !state.plan) { $('qa-eval-plan').value = state.plans[0].id; choosePlan(); }
  }

  async function generate() {
    if (state.busy) return;
    $('qa-sampling-error').textContent = '';
    const button = $('qa-generate');
    try {
      const form = credentials();
      form.append('start', $('qa-start').value);
      form.append('end', $('qa-end').value);
      form.append('evaluators', JSON.stringify($('qa-evaluators').value.split('\n')));
      form.append('market', $('qa-market').value || 'GH');
      if ($('qa-campaign').value.trim()) form.append('campaign', $('qa-campaign').value.trim());
      state.busy = true; button.disabled = true; button.classList.add('loading'); button.textContent = 'Reading recordings…';
      const plan = await request('/api/qa/plans', {method: 'POST', body: form}, 'Unable to build the sample.');
      $('qa-plan').innerHTML = planHtml(plan);
      await loadPlans();
      $('qa-eval-plan').value = plan.id;
      choosePlan();
    } catch (error) {
      $('qa-sampling-error').textContent = error.message;
    } finally {
      state.busy = false; button.disabled = false; button.classList.remove('loading'); button.textContent = 'Generate sample';
    }
  }

  // ----- Evaluation -----

  async function choosePlan() {
    stopPlayer();
    $('qa-eval-error').textContent = ''; $('qa-eval-calls').innerHTML = ''; clearForm();
    const id = $('qa-eval-plan').value;
    state.plan = null;
    $('qa-eval-evaluator').innerHTML = '<option value="">Select your name</option>';
    $('qa-eval-evaluator').disabled = true;
    if (!id) return;
    try {
      state.plan = await request(`/api/qa/plans/${encodeURIComponent(id)}`, {}, 'Unable to load the sample.');
      $('qa-eval-evaluator').innerHTML += state.plan.assignments.map(entry =>
        `<option value="${escapeHtml(entry.evaluator)}">${escapeHtml(entry.evaluator)}</option>`).join('');
      $('qa-eval-evaluator').disabled = false;
    } catch (error) { $('qa-eval-error').textContent = error.message; }
  }

  function myAssignment() {
    return state.plan?.assignments.find(entry => entry.evaluator === $('qa-eval-evaluator').value) || null;
  }

  async function chooseEvaluator() {
    stopPlayer();
    clearForm();
    const assignment = myAssignment();
    if (!assignment) { $('qa-eval-calls').innerHTML = ''; return; }
    try {
      const all = (await request('/api/qa/evaluations', {}, 'Unable to load evaluations.')).evaluations;
      state.evaluations = new Map(all.map(row => [row.recording_id, row]));
    } catch (error) { $('qa-eval-error').textContent = error.message; }
    renderCalls();
  }

  function renderCalls() {
    const assignment = myAssignment();
    if (!assignment) return;
    const total = assignment.agents.reduce((sum, agent) => sum + agent.calls.length, 0);
    const done = assignment.agents.reduce((sum, agent) => sum + agent.calls.filter(c => state.evaluations.has(c.recording_id)).length, 0);
    $('qa-eval-calls').innerHTML = `<div class="card"><p class="section-title">Your calls <span class="count">${done} of ${total} evaluated</span></p>
      ${assignment.agents.map(agent => `<div class="qa-agent"><h3>${cell(agent.agent_name)} <span class="count">ID ${agent.agent_id}</span></h3>
      <div class="table-wrap"><table><thead><tr><th>Listen</th><th>Length</th><th>Duration</th><th>Recording</th><th>Campaign</th><th>Date</th><th>Status</th></tr></thead><tbody>
      ${agent.calls.map(call => {
        const evaluation = state.evaluations.get(call.recording_id);
        const status = evaluation ? `<span class="badge ${evaluation.critical_failed ? 'failed' : 'success'}">${resultText(evaluation)}</span>` : '<span class="badge">To do</span>';
        return `<tr class="qa-call${state.selected?.call.recording_id === call.recording_id ? ' selected' : ''}" tabindex="0" data-recording="${call.recording_id}" data-agent="${agent.agent_id}">
          <td class="qa-listen" data-listen="${call.recording_id}"><button class="button button-secondary small" data-play="${call.recording_id}" aria-label="Play recording ${call.recording_id}">▶ Play</button></td><td><span class="bucket ${call.bucket}">${call.bucket}</span></td><td>${duration(call.duration)}</td><td>${call.recording_id}</td><td class="qa-campaign" title="${escapeHtml(call.campaign || '')}">${escapeHtml(shortCampaign(call.campaign))}</td><td>${cell(when(call.created_at))}</td><td>${status}</td></tr>`;
      }).join('')}</tbody></table></div></div>`).join('')}</div>`;
    document.querySelectorAll('.qa-call').forEach(row => {
      const open = () => selectCall(Number(row.dataset.recording), Number(row.dataset.agent));
      row.addEventListener('click', open);
      row.addEventListener('keydown', event => { if (event.key === 'Enter' && event.target === row) open(); });
    });
    document.querySelectorAll('.qa-listen').forEach(cell => cell.addEventListener('click', event => event.stopPropagation()));
    document.querySelectorAll('[data-play]').forEach(button => button.addEventListener('click', () => playCall(Number(button.dataset.play))));
    // Keep a playing recording in its row when the table is redrawn.
    if (state.player) document.querySelector(`[data-listen="${state.player.recordingId}"]`)?.replaceChildren(state.player.wrap);
  }

  function stopPlayer() {
    if (!state.player) return;
    state.player.audio?.pause();
    URL.revokeObjectURL(state.player.url);
    const cell = document.querySelector(`[data-listen="${state.player.recordingId}"]`);
    state.player = null;
    if (cell) {
      const id = cell.dataset.listen;
      cell.innerHTML = `<button class="button button-secondary small" data-play="${id}">▶ Play</button>`;
      cell.querySelector('[data-play]').addEventListener('click', () => playCall(Number(id)));
    }
  }

  async function playCall(recordingId) {
    const cell = document.querySelector(`[data-listen="${recordingId}"]`);
    if (!cell) return;
    selectCall(recordingId, Number(cell.closest('.qa-call').dataset.agent));
    stopPlayer();
    cell.innerHTML = '<span class="monitor-note">Loading…</span>';
    try {
      const recording = await fetchRecording(recordingId);
      const wrap = document.createElement('div');
      wrap.className = 'qa-player';
      const download = `<a href="${recording.url}" download="recording-${recordingId}.${recording.extension}" title="${escapeHtml(recording.kind)}">Download</a>`;
      if (recording.playable === 'no') {
        wrap.innerHTML = `<span class="qa-play-error">Can't play in browser.</span> ${download}`;
        state.player = {recordingId, url: recording.url, wrap, audio: null};
      } else {
        wrap.innerHTML = `<audio controls autoplay preload="auto"></audio> ${download}`;
        const audio = wrap.querySelector('audio');
        audio.addEventListener('error', () => { audio.replaceWith(Object.assign(document.createElement('span'), {className: 'qa-play-error', textContent: "Can't play in browser."})); }, {once: true});
        audio.src = recording.url;
        state.player = {recordingId, url: recording.url, wrap, audio};
      }
      document.querySelector(`[data-listen="${recordingId}"]`)?.replaceChildren(wrap);
    } catch (error) {
      cell.innerHTML = `<span class="qa-play-error">${escapeHtml(error.message)}</span> <button class="button button-secondary small" data-play="${recordingId}">Retry</button>`;
      cell.querySelector('[data-play]').addEventListener('click', () => playCall(recordingId));
    }
  }

  function clearForm() {
    state.selected = null;
    document.querySelectorAll('.qa-score-row').forEach(row => row.remove());
    document.querySelectorAll('.qa-call.selected').forEach(row => row.classList.remove('selected'));
    $('qa-eval-form').innerHTML = '';
  }

  // Scoring panel opens directly under the call being listened to.
  function selectCall(recordingId, agentId, force = false) {
    if (!force && state.selected?.call.recording_id === recordingId) return;
    clearForm();
    const agent = myAssignment()?.agents.find(a => a.agent_id === agentId);
    const call = agent?.calls.find(c => c.recording_id === recordingId);
    const row = document.querySelector(`.qa-call[data-recording="${recordingId}"]`);
    if (!call || !row) return;
    state.selected = {agent, call};
    row.classList.add('selected');
    const existing = state.evaluations.get(recordingId);
    const card = state.scorecards.find(c => c.id === $('qa-eval-scorecard').value);
    const body = existing
      ? `<div class="notice">Scored by ${escapeHtml(existing.evaluator)}: ${escapeHtml(resultText(existing))}${existing.critical_failed && existing.raw_percent != null ? ` (${existing.raw_percent}% before the critical fail)` : ''}.${existing.critical_reason ? ` Critical fail: ${escapeHtml(existing.critical_reason)}.` : ''}${existing.domains?.length ? `<br>${escapeHtml(domainsText(existing))}` : ''}${existing.comment ? ` Comment: ${escapeHtml(existing.comment)}` : ''}</div>`
      : card ? formHtml(card) : '<div class="notice">Choose a scorecard at the top of the page to score this call.</div>';
    const panel = document.createElement('tr');
    panel.className = 'qa-score-row';
    panel.innerHTML = `<td colspan="${row.children.length}"><div class="qa-score-panel">
      <div class="qa-score-head"><strong>Score recording ${recordingId}</strong>
        <span class="count">${cell(agent.agent_name)} · <span class="bucket ${call.bucket}">${call.bucket}</span> ${duration(call.duration)} · ${escapeHtml(shortCampaign(call.campaign))}</span>
        <button class="button button-secondary small" data-close-score>Close</button></div>
      ${body}
      <p class="qa-prototype">5 = full weight, 3 = half weight, 1 = missed. Critical fail makes the total 0%.</p>
    </div></td>`;
    row.after(panel);
    // The table can scroll sideways; keep the panel the width of what's visible.
    const visible = row.closest('.table-wrap')?.clientWidth;
    if (visible) panel.querySelector('.qa-score-panel').style.width = `${visible}px`;
    panel.querySelector('[data-close-score]').addEventListener('click', clearForm);
    if (!existing && card) {
      panel.querySelector('[data-save-score]').addEventListener('click', () => saveEvaluation(card, panel));
      panel.querySelector('[data-critical]').addEventListener('change', event => {
        panel.querySelector('[data-critical-reason-field]').hidden = event.target.value !== 'yes';
      });
    }
  }

  function formHtml(card) {
    return `<div class="qa-criteria">
      ${byDomain(card.items).map(group => (group.domain ? `<p class="qa-domain">${escapeHtml(group.domain)} <span class="count">${group.weight}%</span></p>` : '') + group.items.map(item => `<label class="qa-criterion"><span>${escapeHtml(item.label)} <span class="count">${item.weight}%</span>${autoFail(item) ? '<span class="qa-critical" title="Graded 1 fails the whole call">AUTO-FAIL</span>' : ''}</span>
        <select data-item="${escapeHtml(item.key)}"><option value="">Select…</option><option value="5">5 · Met fully</option><option value="3">3 · Partly met</option><option value="1">1 · Missed</option><option value="na">N/A</option></select></label>`).join('')).join('')}
      </div>
      <div class="qa-criterion qa-critical-row"><label for="qa-critical-select"><strong>Critical fail</strong> <span class="count">sets the total to 0%</span></label>
        <select id="qa-critical-select" data-critical><option value="no">No</option><option value="yes">Yes</option></select></div>
      <label class="field qa-score-comment" data-critical-reason-field hidden><span>Reason for the critical fail</span><input data-critical-reason placeholder="e.g. fraud risk, wrong amount, abusive language"></label>
      <label class="field qa-score-comment"><span>Comment</span><textarea data-comment rows="2" placeholder="What went well, what to improve"></textarea></label>
      <div class="preview-error" role="alert" data-score-error></div>
      <div class="actions"><button class="button button-primary" data-save-score>Save score</button></div>`;
  }

  async function fetchRecording(recordingId) {
    const response = await fetch(`/api/qa/recordings/${recordingId}/audio`, {method: 'POST', body: credentials()});
    if (!response.ok) throw new Error(errorText(await response.json().catch(() => null), 'Unable to load the recording.'));
    const format = response.headers.get('X-Recording-Format') || 'unknown';
    const codec = response.headers.get('X-Recording-Codec') || 'unknown';
    const blob = await response.blob();
    const extension = response.headers.get('Content-Type')?.includes('wav') ? 'wav' : ({MP3: 'mp3', Ogg: 'ogg', AMR: 'amr'}[format] || 'bin');
    return {
      url: URL.createObjectURL(blob), extension, playable: response.headers.get('X-Recording-Playable'),
      kind: `${format}${codec !== 'unknown' && codec !== format ? `, ${codec}` : ''}, ${Math.round(blob.size / 1024)} KB`,
    };
  }

  async function saveEvaluation(card, panel) {
    const {agent, call} = state.selected;
    const answers = {};
    const missing = [];
    for (const item of card.items) {
      const value = panel.querySelector(`select[data-item="${CSS.escape(item.key)}"]`)?.value;
      if (value) answers[item.key] = value; else missing.push(item.label);
    }
    const error = panel.querySelector('[data-score-error]');
    if (missing.length) { error.textContent = `Choose a grade for: ${missing.join(', ')}.`; return; }
    const criticalFail = panel.querySelector('[data-critical]').value === 'yes';
    const criticalReason = panel.querySelector('[data-critical-reason]').value.trim();
    if (criticalFail && !criticalReason) { error.textContent = 'Give a reason for the critical fail.'; return; }
    const button = panel.querySelector('[data-save-score]');
    try {
      button.disabled = true;
      const saved = await sendJson('/api/qa/evaluations', {
        recording_id: call.recording_id, scorecard_id: card.id, evaluator: $('qa-eval-evaluator').value,
        answers, comment: panel.querySelector('[data-comment]').value, agent_id: agent.agent_id, campaign: call.campaign,
        critical_fail: criticalFail, critical_reason: criticalReason,
      });
      state.evaluations.set(call.recording_id, saved);
      renderCalls();
      selectCall(call.recording_id, agent.agent_id, true);
    } catch (err) {
      error.textContent = err.message;
      button.disabled = false;
    }
  }

  // ----- Wiring -----

  async function opened(tab) {
    try {
      if (!state.loaded.scorecards) { state.loaded.scorecards = true; await loadScorecards(); }
      if (!state.loaded.plans) { state.loaded.plans = true; await loadPlans(); }
      if (!state.loaded.markets) {
        state.loaded.markets = true;
        const status = await request('/api/status');
        $('qa-card-market').innerHTML = '<option value="">All markets</option>' +
          (status.markets || []).map(m => `<option value="${escapeHtml(m.code)}">${escapeHtml(m.name)}</option>`).join('');
        const chosen = $('qa-market').value || 'GH';
        $('qa-market').innerHTML = (status.markets || []).map(m => `<option value="${escapeHtml(m.code)}">${escapeHtml(m.name)} (${escapeHtml(m.code)})</option>`).join('');
        $('qa-market').value = chosen;
      }
    } catch (error) {
      const target = {'qa-sampling': 'qa-sampling-error', 'qa-evaluate': 'qa-eval-error', 'qa-scorecards': 'qa-card-error'}[tab];
      if (target) $(target).textContent = error.message;
    }
  }

  const today = new Date();
  $('qa-end').value = isoDay(today);
  $('qa-start').value = isoDay(new Date(today.getTime() - 6 * 86400000));
  $('qa-generate').addEventListener('click', generate);
  $('qa-card-save').addEventListener('click', saveScorecard);
  // The QA lead's Voice and Chat scorecards, loaded into the box to review before saving.
  async function useTemplate(kind) {
    try {
      if ($('qa-card-items').value.trim() && !confirm('Replace the items you typed with this template?')) return;
      if (!state.templates?.[kind]) state.templates = (await request('/api/qa/scorecard-templates', {}, 'Unable to load templates.')).templates;
      const template = state.templates[kind];
      if (!template) throw new Error('This template is not available yet. Restart the app (stop it and run ./run.sh again), then reload the page.');
      $('qa-card-items').value = byDomain(template.items).map(group =>
        [...(group.domain ? [`# ${group.domain}`] : []),
         ...group.items.map(item => [item.label, item.weight, ...(item.auto_fail ? ['auto-fail'] : [])].join(' | '))].join('\n')).join('\n');
      if (!$('qa-card-name').value.trim()) $('qa-card-name').value = template.name;
      $('qa-card-error').textContent = '';
      $('qa-card-items').focus();
    } catch (error) { $('qa-card-error').textContent = error.message; }
  }
  $('qa-card-collections').addEventListener('click', () => useTemplate('collections'));
  $('qa-card-chat').addEventListener('click', () => useTemplate('chat'));
  $('qa-eval-plan').addEventListener('change', choosePlan);
  $('qa-eval-evaluator').addEventListener('change', chooseEvaluator);
  $('qa-eval-scorecard').addEventListener('change', () => {
    if (state.selected) selectCall(state.selected.call.recording_id, state.selected.agent.agent_id, true);
  });

  window.qaUI = { opened, parseItems };
  const active = document.querySelector('.tab.active');
  if (active?.id?.startsWith('qa-')) opened(active.id);
})();
