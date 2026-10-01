import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.batches import BatchRepository
from app.campaigns import run_batch, segment_name, campaign_for_agent, available_list_name
from app.domain import get_market
from app.history import JsonHistoryRepository
from app.settings import Settings
from app.xcally import XcallyClient, XcallyError

CSV = b'AGENT_NAME,FIRSTNAME,MOBILE_PHONE\nEdward Apersil,One,0000000001\nSamuel Ntow,Two,0000000002\nEdward Apersil,Three,0000000003\n'


class CampaignWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = BatchRepository(root / 'batches.db')
        self.history = JsonHistoryRepository(root / 'history.json')
        self.batch = {'id': 'ec017492-0c52-4b06-ae81-ae7bf4b62ad1', 'status': 'queued', 'file_name': 'test.csv', 'results': []}
        self.repo.create(self.batch, 'fingerprint')
        self.settings = Settings('https://example.invalid', '/api', True, '', '', 1, 1, 60000000, 500)
        self.client = MagicMock(spec=XcallyClient)
        self.client.agents.return_value = [
            {'id': 1, 'role': 'agent', 'name': 'edwarda', 'fullname': 'Edward Apersil'},
            {'id': 2, 'role': 'agent', 'name': 'samueln', 'fullname': 'Samuel Ntow'}]
        self.queues = {201: {'id': 201, 'name': 'GH_AGENT_QUEUE_EDWARDA', 'type': 'outbound', 'dialActive': True},
                       202: {'id': 202, 'name': 'GH_AGENT_QUEUE_SAMUELN', 'type': 'outbound', 'dialActive': False}}
        self.client.campaigns.return_value = list(self.queues.values())
        self.client.queue.side_effect = lambda id: dict(self.queues[id])
        self.client.set_campaign_active.side_effect = lambda id, active: self.queues[id].update(dialActive=active)
        self.links = {201: {11, 12}, 202: {13}}
        self.client.list_ids.side_effect = lambda id: set(self.links[id])
        self.client.assign_list.side_effect = lambda id, list_id: self.links[id].add(list_id)
        self.client.detach_lists.side_effect = lambda id, ids: self.links[id].difference_update(ids)
        self.client.create_list.side_effect = [{'id': 101}, {'id': 102}]
        self.client.fetch_list_fields.return_value = []
        self.client.stage_upload.return_value = {'file': {'name': 'staged.csv'}}
        self.client.finalize_upload.return_value = {'pid': 123}

    def run_workflow(self, contents=CSV, assignments=None):
        with patch('app.campaigns.XcallyClient', return_value=self.client):
            run_batch(self.batch, contents, 'AGENT_NAME', assignments or {'Edward Apersil': 1, 'Samuel Ntow': 2},
                      'Collections', get_market('GH'), self.settings, 'operator', 'secret-test-password', self.repo, self.history)
        return self.repo.get(self.batch['id'])

    def test_replaces_links_and_retains_campaign_settings(self):
        uploaded = []
        def stage(path, name):
            with path.open() as handle:
                uploaded.append(list(csv.DictReader(handle)))
            return {'file': {'name': name}}
        self.client.stage_upload.side_effect = stage
        result = self.run_workflow()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual([len(rows) for rows in uploaded], [2, 1])
        self.assertEqual([r['FIRSTNAME'] for r in uploaded[0]], ['One', 'Three'])
        self.assertNotIn('AGENT_NAME', uploaded[0][0])
        self.assertEqual(self.links, {201: {101}, 202: {102}})
        self.assertEqual([c.args for c in self.client.detach_lists.call_args_list], [(201, [11, 12]), (202, [13])])
        self.assertEqual([c.args for c in self.client.set_campaign_active.call_args_list], [(201, False), (201, True)])
        self.assertTrue(self.queues[201]['dialActive'])
        self.assertFalse(self.queues[202]['dialActive'])
        self.assertEqual(result['results'][0]['detached_list_ids'], [11, 12])
        calls = [c[0] for c in self.client.method_calls]
        self.assertLess(calls.index('wait_for_import'), calls.index('assign_list'))
        self.assertLess(calls.index('detach_lists'), calls.index('assign_list'))
        self.assertEqual(len(self.history.list()), 2)
        self.assertNotIn('secret-test-password', str(result) + str(self.history.list()))

    def test_import_failure_leaves_old_links_and_active_state_untouched(self):
        self.client.wait_for_import.side_effect = XcallyError('Incomplete import')
        result = self.run_workflow()
        self.assertEqual(result['status'], 'partial_failure')
        self.client.assign_list.assert_not_called()
        self.client.detach_lists.assert_not_called()
        self.client.set_campaign_active.assert_not_called()
        self.assertEqual(self.links, {201: {11, 12}, 202: {13}})

    def test_attachment_failure_keeps_campaign_paused_and_records_detached_ids(self):
        self.client.assign_list.side_effect = XcallyError('Attachment failed')
        result = self.run_workflow()
        self.assertEqual(self.links[201], set())
        self.assertEqual(result['results'][0]['detached_list_ids'], [11, 12])
        self.assertFalse(self.queues[201]['dialActive'])
        self.assertIn('may be paused', result['results'][0]['message'])
        self.assertEqual(result['results'][0]['previous_list_ids'], [11, 12])

    def test_detachment_failure_stays_paused_and_preserves_ids(self):
        def detach(queue_id, ids):
            if queue_id == 201:
                raise XcallyError('Detach failed')
            self.links[queue_id].difference_update(ids)
        self.client.detach_lists.side_effect = detach
        result = self.run_workflow()
        self.assertEqual(result['status'], 'partial_failure')
        self.assertEqual(result['results'][1]['status'], 'success')
        self.assertEqual(self.links[201], {11, 12})
        self.assertFalse(self.queues[201]['dialActive'])
        self.assertEqual(result['results'][0]['step'], 'detaching_previous_lists')

    def test_invalid_agent_duplicate_assignment_or_missing_campaign_creates_nothing(self):
        for assignments in [{'Edward Apersil': 999, 'Samuel Ntow': 2}, {'Edward Apersil': 1, 'Samuel Ntow': 1}]:
            self.assertEqual(self.run_workflow(assignments=assignments)['status'], 'failed')
            self.client.create_list.assert_not_called()
        self.client.campaigns.return_value = []
        self.assertEqual(self.run_workflow()['status'], 'failed')
        self.client.create_list.assert_not_called()

    def test_blank_phone_and_cross_agent_duplicates_fail_before_creation(self):
        for contents in [CSV.replace(b'0000000002', b''), CSV.replace(b'0000000002', b'0000000001')]:
            self.assertEqual(self.run_workflow(contents=contents)['status'], 'failed')
            self.client.create_list.assert_not_called()

    def test_agent_membership_must_already_be_correct(self):
        self.client.verify_agent.side_effect = XcallyError('Wrong agent')
        self.assertEqual(self.run_workflow()['status'], 'failed')
        self.client.create_list.assert_not_called()

    def test_matching_is_market_specific_and_ambiguity_rejected(self):
        found = campaign_for_agent(list(self.queues.values()), 'GH', 'edwarda')
        self.assertEqual(found['id'], 201)
        for candidates, market in [(list(self.queues.values()), 'UG'), ([found, found], 'GH')]:
            with self.assertRaises(ValueError):
                campaign_for_agent(candidates, market, 'edwarda')
        name = segment_name('Gh', 'x' * 50, 'Christopher Apusika')
        self.assertIn('Christopher_Apusika', name)
        self.assertLessEqual(len(name), 100)


class BatchRepositoryTests(unittest.TestCase):
    def test_repeated_submission_restart_and_campaign_locking(self):
        with tempfile.TemporaryDirectory() as root:
            repo = BatchRepository(Path(root) / 'batches.db')
            batch = {'id': 'batch', 'status': 'queued', 'results': []}
            self.assertTrue(repo.create(batch, 'a')[1])
            self.assertFalse(repo.create(batch, 'a')[1])
            with self.assertRaises(ValueError):
                repo.create(batch, 'b')
            repo.lock_campaigns('batch', [1, 2])
            with self.assertRaises(ValueError):
                repo.lock_campaigns('other', [3, 1])
            repo.lock_campaigns('third', [3])  # Failed reservation rolled back completely.
            repo.unlock_campaigns('batch')
            repo.lock_campaigns('other', [1, 2])
            repo.interrupt_unfinished()
            self.assertEqual(repo.get('batch')['status'], 'interrupted')


class CampaignTransportTests(unittest.TestCase):
    def setUp(self):
        self.client = XcallyClient(Settings('https://example.invalid', '/api', True, '', '', 1, 1, 1, 1, 1), 'user', 'pass')
        self.addCleanup(self.client.close)

    def test_detaches_association_endpoint_only(self):
        self.client.request = MagicMock()
        self.client.detach_lists(20, [7, 8])
        self.client.request.assert_called_once_with('DELETE', '/voice/queues/20/lists', params={'ids': [7, 8]})
        self.client.json_request = MagicMock()
        self.client.assign_list(20, 9)
        self.client.json_request.assert_called_once_with('POST', '/voice/queues/20/lists', json={'id': 20, 'ids': [9]})

    def test_checks_exclusive_agent_membership(self):
        self.client.collection = MagicMock(side_effect=[[{'id': 4}, {'id': 99}], []])
        with self.assertRaisesRegex(XcallyError, 'designated agent'):
            self.client.verify_agent(20, 4)

    def test_active_campaign_options_filter_upstream_and_locally(self):
        self.client.collection = MagicMock(return_value=[
            {'id': 1, 'name': 'Active', 'type': 'outbound', 'dialActive': True},
            {'id': 2, 'name': 'Paused', 'type': 'outbound', 'dialActive': False},
        ])
        self.assertEqual([row['id'] for row in self.client.campaigns(active_only=True)], [1])
        self.assertEqual(self.client.collection.call_args.kwargs['dialActive'], True)

    def test_monitor_and_agent_page_match_realtime_by_id(self):
        self.client.queue = MagicMock(return_value={'id': 20, 'name': 'Campaign', 'type': 'outbound', 'dialActive': True})
        data = {
            '/voice/queues/20/users': [{'id': 4, 'name': 'Agent'}, {'id': 5, 'name': 'Second'}, {'id': 6, 'name': 'Third'}],
            '/voice/queues/20/lists': [{'id': 9}],
            '/realtime/agents': [
                {'id': 3, 'online': True, 'voiceStatus': 'idle'},
                {'id': 4, 'online': False, 'voiceStatus': 'unknown'},
                {'id': 5, 'online': True, 'voiceStatus': 'idle'},
                {'id': 6, 'online': True, 'voiceStatus': 'talking'},
            ],
        }
        self.client.collection = MagicMock(side_effect=lambda endpoint: data[endpoint])
        self.client.realtime_agents = MagicMock(return_value=data['/realtime/agents'])
        self.client.campaign_recent_calls = MagicMock(return_value=[{'status': 'Answer', 'started_at': '2026-10-01T10:04:17Z', 'ended_at': '2026-10-01T10:05:11Z'}])
        result = self.client.campaign_monitor_snapshot(20)
        self.assertEqual(result['realtime'], {'logged_in': 2, 'available': 1, 'talking': 1, 'ringing': 0})
        self.assertEqual(result['recent_calls'][0]['status'], 'Answer')
        agent_result = self.client.campaign_agent_status(20)
        self.assertEqual(agent_result['agents'][0]['online'], False)
        self.assertEqual(agent_result['agents'][0]['voice_status'], 'unknown')

    def test_missing_agent_presence_does_not_become_zero(self):
        self.client.queue = MagicMock(return_value={'id': 20, 'name': 'Campaign', 'type': 'outbound', 'dialActive': True})
        self.client.collection = MagicMock(side_effect=[[{'id': 4, 'name': 'Agent'}], [{'id': 9}]])
        self.client.realtime_agents = MagicMock(return_value=[])
        self.client.campaign_recent_calls = MagicMock(return_value=[])
        result = self.client.campaign_monitor_snapshot(20)
        self.assertIsNone(result['realtime']['available'])
        self.assertIsNone(result['realtime']['logged_in'])
        self.assertIn('None of this campaign', result['agents_error'])

    def test_realtime_routes_use_api_prefix(self):
        self.client.session.request = MagicMock()
        self.client.request('GET', '/realtime/agents')
        self.assertEqual(self.client.session.request.call_args.kwargs['url'],
                         'https://example.invalid/api/realtime/agents')

    def test_realtime_agents_uses_confirmed_voice_request(self):
        response = MagicMock()
        response.ok = True
        response.json.return_value = {'count': 1, 'rows': [{'id': 4, 'online': True, 'voiceStatus': 'idle'}]}
        self.client.request = MagicMock(return_value=response)
        self.assertEqual(self.client.realtime_agents()[0]['id'], 4)
        self.assertEqual(self.client.request.call_args.args, ('GET', '/realtime/agents'))
        self.assertEqual(self.client.request.call_args.kwargs['params']['channel'], 'voice')
        self.assertEqual(self.client.request.call_args.kwargs['params']['nolimit'], 'true')

    def test_recent_calls_are_bounded_and_campaign_scoped(self):
        response = MagicMock()
        response.ok = True
        response.json.return_value = {'count': 471206, 'rows': [
            {'VoiceQueueId': 20, 'statedesc': 'NoAnswer', 'starttime': '2026-10-01T10:04:58Z', 'endtime': '2026-10-01T10:05:28Z'}]}
        self.client.request = MagicMock(return_value=response)
        calls = self.client.campaign_recent_calls(20)
        self.assertEqual(calls, [{'status': 'NoAnswer', 'started_at': '2026-10-01T10:04:58Z', 'ended_at': '2026-10-01T10:05:28Z'}])
        self.assertEqual(self.client.request.call_args.args, ('GET', '/voice/queues/20/hopper_histories'))
        self.assertEqual(self.client.request.call_args.kwargs['params']['VoiceQueueId'], 20)
        self.assertEqual(self.client.request.call_args.kwargs['params']['limit'], 10)
        response.json.return_value['rows'][0]['VoiceQueueId'] = 21
        with self.assertRaisesRegex(XcallyError, 'different campaign'):
            self.client.campaign_recent_calls(20)

    def test_import_count_verification(self):
        def response(data, headers=None):
            value = MagicMock()
            value.ok = True
            value.json.return_value = data
            value.headers = headers or {}
            return value
        self.client.request = MagicMock(side_effect=[response({'count': 0}), response({'count': '20'})])
        with patch('app.xcally.time.sleep'):
            self.client.wait_for_import(1, 20)
        self.assertEqual(self.client.request.call_count, 2)
        call = self.client.request.call_args
        self.assertEqual(call.args, ('GET', '/cm/contacts'))
        self.assertEqual(call.kwargs['params']['ListId'], 1)
        self.client.request.side_effect = None
        self.client.request.return_value = response([{'id': 7}], {'Content-Range': '0-1/20'})
        self.client.wait_for_import(1, 20)
        self.client.request.return_value = response([{'id': 7}])
        with self.assertRaisesRegex(XcallyError, 'pagination metadata'):
            self.client.wait_for_import(1, 20)
        self.client.request.return_value = response({'count': 21})
        with self.assertRaisesRegex(XcallyError, '21 of 20'):
            self.client.wait_for_import(1, 20)

    def test_readable_names_have_only_collision_suffixes(self):
        name = segment_name('Gh', 'Contacts', 'Edmund Sarpong')
        self.assertRegex(name, r'^Gh_\d{8}_Contacts_Edmund_Sarpong$')
        self.client.collection = MagicMock(return_value=[])
        self.assertEqual(available_list_name(self.client, name), name)
        self.client.collection.return_value = [{'name': name}, {'name': name + '_2'}]
        self.assertEqual(available_list_name(self.client, name), name + '_3')

    def test_pagination_handles_server_page_caps(self):
        first, second = MagicMock(), MagicMock()
        first.ok = second.ok = True
        first.json.return_value = {'count': 3, 'rows': [{'id': 1}, {'id': 2}]}
        second.json.return_value = {'count': 3, 'rows': [{'id': 3}]}
        self.client.request = MagicMock(side_effect=[first, second])
        self.assertEqual(len(self.client.collection('/users')), 3)
        self.assertEqual(self.client.request.call_args.kwargs['params']['offset'], 2)
