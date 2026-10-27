"""Exercise real ASGI routing and multipart parsing without HTTP test dependencies."""

import asyncio
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

import main
from app.batches import BatchRepository


async def request(method, path, data=None, file=None):
    prepared = requests.Request(method, 'http://test' + path, data=data,
                                files={'file': ('contacts.csv', file, 'text/csv')} if file else None).prepare()
    body = prepared.body or b''
    if isinstance(body, str):
        body = body.encode()
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
             'method': method, 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
             'query_string': b'', 'root_path': '', 'headers': [(k.lower().encode(), v.encode()) for k, v in prepared.headers.items()],
             'server': ('test', 80), 'client': ('test', 1)}
    messages = []
    received = False
    async def receive():
        nonlocal received
        if received:
            await asyncio.Event().wait()
        received = True
        return {'type': 'http.request', 'body': body, 'more_body': False}
    async def send(message):
        messages.append(message)
    await main.app(scope, receive, send)
    status = next(m['status'] for m in messages if m['type'] == 'http.response.start')
    result = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
    return status, json.loads(result.splitlines()[-1])


class CampaignApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.repo = BatchRepository(Path(self.temp.name) / 'batch.db')
        self.work = MagicMock()
        self.patches = [patch.object(main, 'batch_repository', self.repo), patch.object(main, 'executor', self.pool),
                        patch.object(main, 'settings', replace(main.settings, username='', password='', max_upload_bytes=1024)),
                        patch.object(main, 'batch_slots', threading.BoundedSemaphore(2)), patch.object(main, 'run_batch', self.work)]
        for p in self.patches:
            p.start()
        self.data = {'market': 'GH', 'agent_column': 'AGENT_NAME', 'assignments': '{"Edward Apersil":1}',
                     'batch_id': '6433f6b4-d761-47a4-aacb-4d762e10010f',
                     'username': 'operator', 'password': 'secret'}
        self.csv = b'AGENT_NAME,MOBILE_PHONE\nEdward Apersil,000000001\n'

    async def asyncTearDown(self):
        await asyncio.to_thread(self.pool.shutdown, wait=True)
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    async def test_campaign_controls_report_partial_failures_and_release_locks(self):
        with patch.object(main, 'XcallyClient') as factory:
            client = factory.return_value
            client.queue.side_effect = lambda id: {'id': id, 'type': 'outbound' if id != 3 else 'inbound', 'dialActive': False}
            client.set_campaign_active.side_effect = [main.XcallyError('Verification failed'), None]
            status, result = await request('POST', '/api/campaigns/active', {
                **self.data, 'campaign_ids': '[1,2,3]', 'active': 'true'})
            self.assertEqual(status, 200)
            self.assertEqual([r['ok'] for r in result['results']], [False, True, False])
            self.assertEqual(client.set_campaign_active.call_count, 2)
            client.close.assert_called_once()
            self.repo.lock_campaigns('after', [1, 2, 3])
            self.repo.unlock_campaigns('after')

    async def test_campaign_controls_reject_invalid_ids_and_locked_campaigns(self):
        with patch.object(main, 'XcallyClient') as factory:
            for ids in ('[]', '[1,1]', '[true]', '[0]', '{}', 'invalid'):
                status, _ = await request('POST', '/api/campaigns/active', {
                    **self.data, 'campaign_ids': ids, 'active': 'false'})
                self.assertEqual(status, 422)
            self.repo.lock_campaigns('upload', [2])
            status, _ = await request('POST', '/api/campaigns/active', {
                **self.data, 'campaign_ids': '[1,2]', 'active': 'false'})
            self.assertEqual(status, 409)
            factory.assert_not_called()
            self.repo.lock_campaigns('other', [1])
            self.repo.unlock_campaigns('other')

    async def test_campaign_controls_skip_already_correct_state(self):
        with patch.object(main, 'XcallyClient') as factory:
            factory.return_value.queue.return_value = {'id': 1, 'type': 'outbound', 'dialActive': False}
            status, result = await request('POST', '/api/campaigns/active', {
                **self.data, 'campaign_ids': '[1]', 'active': 'false'})
            self.assertEqual(status, 200)
            self.assertTrue(result['results'][0]['ok'])
            factory.return_value.set_campaign_active.assert_not_called()

    async def test_submission_is_idempotent_and_pollable(self):
        status, result = await request('POST', '/api/campaign-batches', self.data, self.csv)
        self.assertEqual(status, 202)
        status, repeated = await request('POST', '/api/campaign-batches', self.data, self.csv)
        self.assertEqual(status, 202)
        await asyncio.to_thread(self.pool.shutdown, wait=True)
        self.assertEqual(self.work.call_count, 1)
        status, stored = await request('GET', '/api/campaign-batches/' + result['id'])
        self.assertEqual(status, 200)
        self.assertEqual(stored['id'], repeated['id'])
        self.assertNotIn('secret', json.dumps(stored))

    async def test_same_id_with_different_request_is_rejected(self):
        await request('POST', '/api/campaign-batches', self.data, self.csv)
        status, result = await request('POST', '/api/campaign-batches', {**self.data, 'market': 'ZA'}, self.csv)
        self.assertEqual(status, 422)
        self.assertIn('different request', result['detail'])

    async def test_credentials_and_size_limits(self):
        status, _ = await request('POST', '/api/campaign-batches', {**self.data, 'username': '', 'password': ''}, self.csv)
        self.assertEqual(status, 401)
        status, _ = await request('POST', '/api/campaign-batches', self.data, b'x' * 1025)
        self.assertEqual(status, 413)
        self.work.assert_not_called()

    async def test_options_uses_credentials_and_closes_connection(self):
        client = MagicMock()
        client.agents.return_value = [{'id': 1, 'fullname': 'Edward Apersil'}]
        client.campaigns.return_value = [{'id': 3, 'name': 'Collections'}]
        with patch.object(main, 'XcallyClient', return_value=client) as constructor:
            status, data = await request('POST', '/api/campaigns/options', {'username': 'operator', 'password': 'secret'})
        self.assertEqual(status, 200)
        self.assertEqual(data['campaigns'][0]['id'], 3)
        self.assertEqual(constructor.call_args.args[1:], ('operator', 'secret'))
        client.close.assert_called_once()

    async def test_performance_routes_are_read_only_and_scoped(self):
        client = MagicMock()
        client.campaigns.return_value = [{'id': 7, 'name': 'Z campaign'}, {'id': 3, 'name': 'A campaign'}]
        client.campaign_monitor_snapshot.return_value = {'id': 3, 'name': 'A campaign', 'active': True, 'agents': [], 'list_count': 1}
        client.campaign_agent_status.return_value = {'id': 3, 'name': 'A campaign', 'agents': [{'id': 4, 'name': 'Agent', 'online': True, 'voice_status': 'idle'}]}
        client.campaign_hourly_answered.return_value = {'period_start': '2026-10-02T07:00:00+00:00', 'period_end': '2026-10-02T19:00:00+00:00', 'as_of': '2026-10-02T10:30:00+00:00', 'timezone': 'Africa/Accra', 'hours': [], 'totals': {4: 3}}
        client.iter_campaign_hourly_answered.return_value = iter([
            {'processed': 0, 'complete': False}, {'processed': 250, 'complete': False},
            {'processed': 250, 'complete': True},
        ])
        with patch.object(main, 'XcallyClient', return_value=client):
            status, data = await request('POST', '/api/performance/campaigns', {'username': 'operator', 'password': 'secret'})
            self.assertEqual(status, 200)
            self.assertEqual([row['id'] for row in data['campaigns']], [3, 7])
            client.campaigns.assert_called_once_with(active_only=True)
            status, data = await request('POST', '/api/performance/campaigns/3', {'username': 'operator', 'password': 'secret'})
            self.assertEqual(status, 200)
            self.assertEqual(data['id'], 3)
            client.campaign_monitor_snapshot.assert_called_once_with(3)
            status, data = await request('POST', '/api/performance/campaigns/3/agents', {'username': 'operator', 'password': 'secret'})
            self.assertEqual(status, 200)
            self.assertEqual(data['agents'][0]['voice_status'], 'idle')
            client.campaign_agent_status.assert_called_once_with(3)
            status, data = await request('POST', '/api/performance/campaigns/3/hourly-calls', {'username': 'operator', 'password': 'secret'})
            self.assertEqual(status, 200)
            self.assertEqual(data['totals']['4'], 3)
            client.campaign_hourly_answered.assert_called_once_with(3)
            status, data = await request('POST', '/api/performance/campaigns/3/hourly-calls/stream', {'username': 'operator', 'password': 'secret'})
            self.assertEqual(status, 200)
            self.assertEqual(data['complete'], True)
            client.iter_campaign_hourly_answered.assert_called_once_with(3)
            status, _ = await request('POST', '/api/performance/campaigns/0', {'username': 'operator', 'password': 'secret'})
            self.assertEqual(status, 422)
            self.assertEqual(client.campaign_monitor_snapshot.call_count, 1)
        self.assertEqual(client.close.call_count, 5)
