import random
import unittest
from collections import Counter
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.settings import Settings
from app.xcally import XcallyClient, XcallyError

from app.qa.sampling import assign_agents, bucket, build_plan, duration_seconds, sample_agent_calls


def rec(identity, agent, seconds):
    """Recording as xCALLY returns it: duration in milliseconds."""
    duration = None if seconds is None else seconds * 1000
    return {"id": identity, "UserId": agent, "duration": duration, "queue": "GH_Q", "createdAt": "2026-11-10T09:00:00Z"}


class BucketTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(bucket(0), "short")
        self.assertEqual(bucket(119), "short")
        self.assertEqual(bucket(120), "medium")
        self.assertEqual(bucket(300), "medium")
        self.assertEqual(bucket(301), "long")
        self.assertIsNone(bucket(None))
        self.assertIsNone(bucket(-1))

    def test_xcally_duration_is_milliseconds(self):
        # Real values from the live server: 106420 ms is 1:46, not 29 hours.
        self.assertEqual(duration_seconds({"duration": 106420}), 106.42)
        self.assertEqual(bucket(duration_seconds({"duration": 106420})), "short")
        self.assertEqual(bucket(duration_seconds({"duration": 171860})), "medium")
        self.assertIsNone(duration_seconds({"duration": None}))
        self.assertIsNone(duration_seconds({"duration": "100"}))
        plan = build_plan([rec(1, 7, 400)], ["Ama"], seed="x")
        self.assertEqual(plan["assignments"][0]["agents"][0]["calls"][0]["duration"], 400)


class SampleAgentTests(unittest.TestCase):
    def test_four_calls_cover_every_length(self):
        recordings = [rec(i, 1, d) for i, d in enumerate([30, 60, 90, 150, 200, 250, 400, 600, 900])]
        result = sample_agent_calls(recordings, random.Random(1))
        self.assertEqual(len(result["calls"]), 4)
        self.assertTrue({"short", "medium", "long"} <= {c["bucket"] for c in result["calls"]})
        self.assertEqual(len({c["recording_id"] for c in result["calls"]}), 4)
        self.assertEqual((result["missing"], result["short_of_target"]), ([], 0))

    def test_fourth_call_is_a_second_long_call_when_available(self):
        recordings = [rec(i, 1, d) for i, d in enumerate([30, 60, 90, 150, 200, 250, 400, 600, 900])]
        for seed in range(20):
            buckets = sorted(c["bucket"] for c in sample_agent_calls(recordings, random.Random(seed))["calls"])
            self.assertEqual(buckets, ["long", "long", "medium", "short"])

    def test_one_long_call_means_fourth_is_any_length(self):
        recordings = [rec(i, 1, d) for i, d in enumerate([30, 60, 90, 150, 200, 400])]
        calls = sample_agent_calls(recordings, random.Random(1))["calls"]
        self.assertEqual(len(calls), 4)
        self.assertEqual([c["bucket"] for c in calls].count("long"), 1)

    def test_missing_group_is_reported_and_filled(self):
        recordings = [rec(i, 1, d) for i, d in enumerate([30, 60, 90, 150, 200, 250])]
        result = sample_agent_calls(recordings, random.Random(1))
        self.assertEqual(len(result["calls"]), 4)
        self.assertEqual(result["missing"], ["long"])

    def test_too_few_calls(self):
        result = sample_agent_calls([rec(1, 1, 30), rec(2, 1, 400)], random.Random(1))
        self.assertEqual(len(result["calls"]), 2)
        self.assertEqual((result["missing"], result["short_of_target"]), (["medium"], 2))


class AssignmentTests(unittest.TestCase):
    def test_even_split(self):
        result = assign_agents(list(range(10)), ["Ama", "Kofi", "Esi"], random.Random(1))
        self.assertEqual(sorted(len(a) for a in result.values()), [3, 3, 4])
        self.assertEqual(sorted(a for agents in result.values() for a in agents), list(range(10)))

    def test_requires_evaluator(self):
        with self.assertRaises(ValueError):
            assign_agents([1], ["  "], random.Random(1))


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.recordings = [rec(agent * 100 + i, agent, d) for agent in (1, 2, 3)
                           for i, d in enumerate([30, 60, 150, 200, 400, 700])]

    def test_only_campaigns_starting_with_market_code(self):
        recordings = [rec(1, 7, 30), rec(2, 7, 150), rec(3, 7, 400)]
        recordings += [dict(rec(4, 7, 30), queue="UG_QUEUE_X"), dict(rec(5, 7, 30), queue=None),
                       dict(rec(6, 7, 30), queue="gh_lowercase_ok")]
        plan = build_plan(recordings, ["Ama"], seed="x", campaign_prefix="GH")
        picked = {c["recording_id"] for e in plan["assignments"] for a in e["agents"] for c in a["calls"]}
        self.assertEqual(picked, {1, 2, 3, 6})
        self.assertEqual(plan["skipped"]["other_market"], 2)
        self.assertEqual(plan["thresholds"]["campaign_prefix"], "GH")

    def test_plan_excludes_evaluated_and_is_reproducible(self):
        extra = [rec(999, None, 50), rec(998, 1, None), rec(997, 1, 0), rec(996, 1, 0.9)]
        plan = build_plan(self.recordings + extra, ["Ama", "Kofi"], excluded_recording_ids={100}, seed="fixed")
        self.assertEqual(plan["skipped"], {"already_evaluated": 1, "no_agent": 1, "no_duration": 1, "empty": 2, "other_market": 0})
        picked = [c["recording_id"] for e in plan["assignments"] for a in e["agents"] for c in a["calls"]]
        self.assertTrue({100, 997, 996}.isdisjoint(picked))
        self.assertEqual(Counter(len(a["calls"]) for e in plan["assignments"] for a in e["agents"]), {4: 3})
        self.assertEqual(plan, build_plan(self.recordings + extra, ["Ama", "Kofi"],
                                          excluded_recording_ids={100}, seed="fixed"))


class RecordingFetchTests(unittest.TestCase):
    def setUp(self):
        self.client = XcallyClient(Settings('https://example.invalid', '/api', True, '', '', 1, 1, 1, 1, 1), 'user', 'pass')
        self.addCleanup(self.client.close)
        self.start = datetime(2026, 11, 3, tzinfo=timezone.utc)
        self.end = datetime(2026, 11, 10, tzinfo=timezone.utc)

    def respond(self, rows, count=None):
        response = MagicMock(ok=True)
        response.json.return_value = {"count": count if count is not None else len(rows), "rows": rows}
        return response

    def test_keeps_rows_in_range_and_stops_at_start(self):
        rows = [{"id": 3, "createdAt": "2026-11-10T08:00:00Z"},
                {"id": 2, "createdAt": "2026-11-05T08:00:00Z"},
                {"id": 1, "createdAt": "2026-11-01T08:00:00Z"}]
        self.client.request = MagicMock(return_value=self.respond(rows, count=5000))
        self.assertEqual([r["id"] for r in self.client.voice_recordings(self.start, self.end)], [2])
        self.assertEqual(self.client.request.call_count, 1)
        self.assertEqual(self.client.request.call_args.args, ("GET", "/voice/recordings"))

    def test_rejects_other_campaign_and_bad_range(self):
        self.client.request = MagicMock(return_value=self.respond([{"id": 1, "queue": "OTHER", "createdAt": "2026-11-05T08:00:00Z"}]))
        with self.assertRaises(XcallyError):
            self.client.voice_recordings(self.start, self.end, queue_name="GH_Q")
        with self.assertRaises(ValueError):
            self.client.voice_recordings(self.end, self.start)


if __name__ == "__main__":
    unittest.main()
