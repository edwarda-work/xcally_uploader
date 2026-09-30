"""Replace list associations on existing agent campaigns; never delete contacts."""

import csv
import io
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .segmentation import preview_segments
from .xcally import XcallyClient, XcallyError, build_binding


def campaign_for_agent(campaigns: list[dict], market_code: str, username: str) -> dict:
    expected = f'{market_code}_AGENT_QUEUE_{username}'.casefold()
    matches = [c for c in campaigns if c.get('type') == 'outbound' and str(c.get('name', '')).casefold() == expected]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one campaign named {market_code}_AGENT_QUEUE_{username}; found {len(matches)}. Check the campaign name in xCALLY.')
    return matches[0]


def replace_campaign_list(client, result, save):
    queue_id, new_id = result['campaign_id'], result['list_id']
    queue = client.queue(queue_id)
    if queue.get('type') != 'outbound' or queue.get('name') != result['campaign_name']:
        raise XcallyError('The selected campaign changed during upload. Existing lists were not changed.')
    client.verify_agent(queue_id, result['agent_id'])
    original_active = queue.get('dialActive')
    if original_active not in (True, False, 0, 1):
        raise XcallyError('The campaign active status is unknown. Existing lists were not changed.')
    old_ids = client.list_ids(queue_id)
    result['previous_list_ids'] = sorted(old_ids)
    result['original_active'] = bool(original_active)
    result['campaign_paused'] = False
    result['step'] = 'pausing_campaign' if original_active else 'attaching_new_list'
    save()
    # Quiesce dialing during the non-atomic association swap. Never change agents.
    if original_active:
        result['campaign_paused'] = True  # May have reached the server even on timeout.
        save()
        client.set_campaign_active(queue_id, False)
    if client.list_ids(queue_id) != old_ids:
        raise XcallyError('Campaign lists changed during upload. Review the campaign before retrying.')
    # Detach first: attaching while old contacts remain in the hopper can cause
    # duplicate checks to skip replacement contacts. The CSV is already verified.
    result['step'] = 'detaching_previous_lists'
    save()
    client.detach_lists(queue_id, sorted(old_ids - {new_id}))
    if client.list_ids(queue_id) != old_ids & {new_id}:
        raise XcallyError('Previous list detachment could not be verified. Review campaign associations in xCALLY.')
    result['detached_list_ids'] = sorted(old_ids - {new_id})
    result['step'] = 'attaching_new_list'
    save()
    client.assign_list(queue_id, new_id)
    if client.list_ids(queue_id) != {new_id}:
        raise XcallyError('New list attachment could not be verified. Review the retained list IDs in xCALLY.')
    result['step'] = 'restoring_campaign_status'
    save()
    if original_active:
        client.set_campaign_active(queue_id, True)
        result['campaign_paused'] = False
    elif client.queue(queue_id).get('dialActive') not in (False, 0):
        raise XcallyError('Campaign was activated elsewhere during replacement. Review its status in xCALLY.')
    save()


def validate_assignments(preview: dict, assignments: dict, agents: list[dict]) -> None:
    names = {segment['agent'] for segment in preview['segments']}
    if not preview['agent_column'] or preview['unassigned']:
        raise ValueError('Every contact must have an agent before replacing campaign lists.')
    if not names or set(assignments) != names:
        raise ValueError('Match every CSV agent to an xCALLY agent.')
    valid = {agent['id'] for agent in agents if agent.get('role') == 'agent'}
    ids = list(assignments.values())
    if any(type(agent_id) is not int or agent_id not in valid for agent_id in ids):
        raise ValueError('An assigned xCALLY agent is unavailable. Reload agents and check the matches.')
    if len(set(ids)) != len(ids):
        raise ValueError('Each segment must match a different agent. Combine duplicate agent names in the CSV first.')


def segment_name(prefix: str, base: str, agent: str) -> str:
    safe_base = re.sub(r'[^A-Za-z0-9_-]+', '_', base).strip('_')[:40] or 'Contacts'
    safe_agent = re.sub(r'[^A-Za-z0-9_-]+', '_', agent).strip('_')[:40] or 'Agent'
    return f'{prefix}_{datetime.now():%Y%m%d}_{safe_base}_{safe_agent}'[:100]


def available_list_name(client, name: str) -> str:
    existing = {row.get('name') for row in client.collection('/cm/lists', fields='id,name', filter=name)}
    candidate, counter = name, 2
    while candidate in existing:
        suffix = f'_{counter}'
        candidate = name[:100-len(suffix)] + suffix
        counter += 1
    return candidate


def run_batch(batch, contents, agent_column, assignments, base_name,
              market, settings, username, password, repository, history):
    client = XcallyClient(settings, username, password)
    batch['status'] = 'running'
    try:
        # Finish all remote preflight reads before creating any resources.
        repository.save(batch)
        preview = preview_segments(contents, agent_column)
        agents = client.agents()
        validate_assignments(preview, assignments, agents)
        agent_names = {a['id']: a.get('fullname') or a.get('name') or f"Agent_{a['id']}" for a in agents}
        remote_campaigns = client.campaigns()
        by_id = {a['id']: a for a in agents}
        matched = {}
        for agent, agent_id in assignments.items():
            account = by_id[agent_id]
            if not account.get('name'):
                raise ValueError('An agent has no xCALLY username.')
            campaign = campaign_for_agent(remote_campaigns, market.code.value, account['name'])
            client.verify_agent(campaign['id'], agent_id)
            matched[agent] = campaign
        repository.lock_campaigns(batch['id'], [c['id'] for c in matched.values()])
        reader = csv.reader(io.StringIO(contents.decode('utf-8-sig'), newline=''), strict=True)
        headers = [h.strip() for h in next(reader)]
        agent_index = headers.index(agent_column)
        contact_headers = [h for i, h in enumerate(headers) if i != agent_index]
        if 'MOBILE_PHONE' not in contact_headers:
            raise ValueError('MOBILE_PHONE is required for campaign contact lists.')
        batch['message'] = 'Uploading replacement lists for existing agent campaigns.'
        # Temporary CSVs are removed together, including on partial failures.
        with tempfile.TemporaryDirectory(prefix='xcally-segments-') as directory:
            paths = {}
            for index, segment in enumerate(preview['segments'], 1):
                name = segment_name(market.list_prefix, base_name, agent_names[assignments[segment['agent']]])
                paths[segment['agent']] = Path(directory) / f'{index}.csv'
                with paths[segment['agent']].open('w', encoding='utf-8', newline='') as handle:
                    csv.writer(handle).writerow(contact_headers)
                batch['results'].append({
                    'file_name': batch['file_name'], 'market': market.code.value,
                    'agent': segment['agent'], 'agent_id': assignments[segment['agent']],
                    'agent_name': agent_names[assignments[segment['agent']]], 'contact_count': segment['count'],
                    'list_name': name, 'campaign_name': matched[segment['agent']]['name'],
                    'list_id': None, 'campaign_id': matched[segment['agent']]['id'],
                    'previous_list_ids': [], 'detached_list_ids': [],
                    'status': 'pending', 'step': 'pending', 'message': '', 'pid': None,
                    'timestamp': datetime.now(timezone.utc).isoformat(), 'batch_id': batch['id'],
                })
            # Bound open descriptors even for large agent sets; buffered per-agent writes.
            buffers = {agent: [] for agent in paths}
            phone_owners = {}
            for row in reader:
                if not any(value.strip() for value in row):
                    continue
                if not row[headers.index('MOBILE_PHONE')].strip():
                    raise ValueError(f'MOBILE_PHONE is blank at CSV line {reader.line_num}.')
                agent = row[agent_index].strip()
                phone = re.sub(r'\D', '', row[headers.index('MOBILE_PHONE')])
                if not phone:
                    raise ValueError(f'MOBILE_PHONE has no digits at CSV line {reader.line_num}.')
                if phone in phone_owners and phone_owners[phone] != agent:
                    raise ValueError(f'A phone number is assigned to multiple agents at CSV line {reader.line_num}. Correct the assignments before replacing campaign lists.')
                phone_owners[phone] = agent
                buffers[agent].append([value for i, value in enumerate(row) if i != agent_index])
                if len(buffers[agent]) >= 500:
                    with paths[agent].open('a', encoding='utf-8', newline='') as handle:
                        csv.writer(handle).writerows(buffers[agent])
                    buffers[agent].clear()
            for agent, rows in buffers.items():
                with paths[agent].open('a', encoding='utf-8', newline='') as handle:
                    csv.writer(handle).writerows(rows)
            repository.save(batch)
            for result in batch['results']:
                def step(name):
                    result['step'] = name
                    result['status'] = 'running'
                    repository.save(batch)

                try:
                    step('checking_list_name')
                    result['list_name'] = available_list_name(client, result['list_name'])
                    step('creating_list')
                    result['list_id'] = int(client.create_list(result['list_name'])['id'])
                    repository.save(batch)
                    binding, skipped = build_binding(contact_headers, client.fetch_list_fields(result['list_id']), market)
                    result['skipped_headers'] = skipped
                    step('importing_contacts')
                    staged = client.stage_upload(paths[result['agent']], result['list_name'] + '.csv')
                    staged_id = staged.get('file', {}).get('name')
                    if not staged_id:
                        raise XcallyError('xCALLY did not return a staged file identifier.')
                    final = client.finalize_upload(result['list_id'], staged_id, binding)
                    result['pid'] = final.get('pid') if type(final.get('pid')) is int else None
                    result['import_status'] = 'submitted'
                    step('verifying_contact_import')
                    client.wait_for_import(result['list_id'], result['contact_count'])
                    result['import_status'] = 'verified'
                    replace_campaign_list(client, result, lambda: repository.save(batch))
                    result.update(status='success', step='complete',
                                  message='New contacts verified and campaign list replaced. Previous lists remain in Contacts Manager; original campaign active status restored.')
                except Exception as exc:
                    result['status'] = 'failed'
                    result['message'] = str(exc) if isinstance(exc, XcallyError) else 'Campaign list replacement could not be completed. Review the recorded resource IDs in xCALLY.'
                    result['message'] += ' No automatic retry was attempted; review the recorded list IDs.'
                    if result.get('campaign_paused'):
                        result['message'] += ' The campaign may be paused. Check its associations and active status in xCALLY before resuming.'
                repository.save(batch)
                try:
                    history.add([dict(result)])
                except OSError:
                    batch['history_warning'] = 'Upload history could not be updated. Results remain in this batch.'
            batch['status'] = 'completed' if all(r['status'] == 'success' for r in batch['results']) else 'partial_failure'
            batch['message'] = 'Finished processing campaign list replacements. Previous lists and contacts were retained.'
    except Exception as exc:
        batch['status'] = 'failed'
        batch['message'] = str(exc) if isinstance(exc, (ValueError, XcallyError)) else 'Batch could not be completed. Review recorded resources before retrying.'
        for result in batch['results']:
            if result['status'] == 'pending':
                result.update(status='failed', step='preflight', message='Not created: batch validation failed.')
    finally:
        client.close()
        repository.unlock_campaigns(batch['id'])
        repository.save(batch)
