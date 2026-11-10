/* Explicit outbound campaign status controls. Credentials remain in memory. */
(() => {
  'use strict';
  let campaigns = [], selected = new Set(), busy = false, generation = 0;
  const message = text => { $('control-message').textContent = text; };
  function credentials() {
    const ownUser = $('campaign-username').value.trim(), ownPassword = $('campaign-password').value;
    const username = ownUser || (ownPassword ? '' : $('username').value.trim());
    const password = ownPassword || (ownUser ? '' : $('password').value);
    const form = new FormData();
    if (username || password) {
      if (!username || !password) throw new Error('Enter both xCALLY username and password.');
      form.append('username', username); form.append('password', password);
    }
    return form;
  }
  async function post(url, form) {
    const response = await fetch(url, {method: 'POST', body: form});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Campaign request failed.');
    return data;
  }
  function visibleCampaigns() {
    const query = $('control-search').value.trim().toLocaleLowerCase();
    const related = new Set(window.campaignUI?.relatedCampaignIds?.() || []);
    const byAgents = $('control-scope').value === 'agents';
    if (!byAgents && !query) return [];
    return campaigns.filter(c => (!byAgents || related.has(c.id)) &&
      (!query || c.name.toLocaleLowerCase().includes(query)));
  }
  function render() {
    const visible = visibleCampaigns();
    const visibleIds = new Set(visible.map(c => c.id));
    selected = new Set([...selected].filter(id => visibleIds.has(id)));
    $('control-load').disabled = busy;
    $('control-search').disabled = $('control-scope').disabled = busy;
    $('control-count').textContent = `${visible.length} matching campaigns · ${selected.size} selected`;
    $('control-on').disabled = $('control-off').disabled = busy || !selected.size;
    const label = c => c.dialActive === true || c.dialActive === 1 ? 'ON'
      : c.dialActive === false || c.dialActive === 0 ? 'OFF' : 'Unknown';
    $('control-campaigns').innerHTML = visible.length ? `<table><thead><tr><th><input type="checkbox" data-control-all aria-label="Select all matching campaigns" ${selected.size === visible.length ? 'checked' : ''} ${busy ? 'disabled' : ''}></th><th>Campaign</th><th>Last checked status</th><th>Actions</th><th>Result</th></tr></thead><tbody>${visible.map(c => `<tr><td><input type="checkbox" data-control-select="${c.id}" aria-label="Select ${escapeHtml(c.name)}" ${selected.has(c.id) ? 'checked' : ''} ${busy ? 'disabled' : ''}></td><td>${escapeHtml(c.name)}</td><td>${label(c)}</td><td><button class="button button-secondary" data-control-id="${c.id}" data-control-active="true" ${busy ? 'disabled' : ''}>ON</button> <button class="button button-secondary" data-control-id="${c.id}" data-control-active="false" ${busy ? 'disabled' : ''}>OFF</button></td><td>${escapeHtml(c.result || '')}</td></tr>`).join('')}</tbody></table>` : `<p class="help">${!campaigns.length ? "No campaigns loaded." : $("control-scope").value === "agents" ? "No matching campaigns. Preview a CSV, load agents above, and confirm assignments for the selected market." : "Enter a campaign name to find matching campaigns."}</p>`;
  }
  async function load() {
    if (busy) return;
    const version = generation;
    busy = true; render(); message('Loading campaign status…');
    try {
      const data = await post('/api/campaigns/options', credentials());
      if (version !== generation) return;
      if (!Array.isArray(data.campaigns) || data.campaigns.some(c => !c || !Number.isInteger(c.id) || c.id <= 0 || typeof c.name !== 'string')) throw new Error('Invalid campaign response.');
      campaigns = data.campaigns.filter(c => c.type === 'outbound').sort((a,b) => a.name.localeCompare(b.name));
      selected.clear(); message(`${campaigns.length} campaigns loaded. Select campaigns or use individual ON/OFF buttons.`);
    } catch (error) { if (version === generation) { campaigns = []; selected.clear(); message(error.message); } }
    finally { busy = false; render(); }
  }
  async function change(ids, active) {
    if (busy || !ids.length) return;
    const visibleIds = new Set(visibleCampaigns().map(c => c.id));
    ids = ids.filter(id => visibleIds.has(id));
    if (!ids.length) return;
    if (ids.length > 100) { message('Select at most 100 campaigns per operation.'); return; }
    if (!window.confirm(`Turn ${ids.length} campaign(s) ${active ? 'ON (enable dialing)' : 'OFF (pause dialing)'}?\n${campaigns.filter(c => ids.includes(c.id)).map(c => c.name).join('\n')}`)) return;
    const version = generation;
    busy = true; render(); message('Updating campaigns. Keep this page open…');
    try {
      const form = credentials();
      form.append('campaign_ids', JSON.stringify(ids)); form.append('active', String(active));
      const data = await post('/api/campaigns/active', form);
      if (version !== generation) return;
      if (!Array.isArray(data.results) || data.results.length !== ids.length || new Set(data.results.map(r => r.id)).size !== ids.length || data.results.some(r => !ids.includes(r.id) || typeof r.ok !== 'boolean' || (r.ok && r.active !== active))) throw new Error('Invalid update response.');
      for (const result of data.results) {
        const campaign = campaigns.find(c => c.id === result.id);
        campaign.dialActive = result.ok ? result.active : null;
        campaign.result = result.ok ? `Verified ${active ? 'ON' : 'OFF'}` : result.error || 'Status could not be verified.';
      }
      message(`${data.results.filter(r => r.ok).length} of ${ids.length} campaigns verified. Review each result before retrying.`);
    } catch (error) {
      if (version === generation) {
        campaigns.filter(c => ids.includes(c.id)).forEach(c => { c.dialActive = null; c.result = 'Status unknown. Reload before retrying.'; });
        message(`${error.message} Reload campaign status before retrying; some changes may have completed.`);
      }
    } finally { busy = false; render(); }
  }
  $('control-load').addEventListener('click', load);
  $('control-on').addEventListener('click', () => change([...selected], true));
  $('control-off').addEventListener('click', () => change([...selected], false));
  $('control-campaigns').addEventListener('change', event => {
    if (busy) return;
    const node = event.target;
    if (node.hasAttribute('data-control-all')) selected = node.checked ? new Set(visibleCampaigns().map(c => c.id)) : new Set();
    else if (node.dataset.controlSelect) {
      const id = Number(node.dataset.controlSelect);
      if (visibleCampaigns().some(c => c.id === id)) node.checked ? selected.add(id) : selected.delete(id);
    }
    render();
  });
  $('control-campaigns').addEventListener('click', event => {
    const node = event.target.closest('[data-control-id]');
    if (node && visibleCampaigns().some(c => c.id === Number(node.dataset.controlId))) return change([Number(node.dataset.controlId)], node.dataset.controlActive === 'true');
  });
  for (const id of ['campaign-username', 'campaign-password', 'username', 'password']) {
    $(id).addEventListener('input', () => {
      generation++; campaigns = []; selected.clear(); render();
      message('Credentials changed. Reload campaign status.');
    });
  }
  $("control-search").addEventListener("input", render);
  $("control-scope").addEventListener("change", () => { selected.clear(); render(); });
  window.campaignControls = {refresh: render};
  render();
})();
