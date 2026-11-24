import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.qa import routes
from app.qa.routes import EvaluationIn, ScorecardIn, create_router, parse_evaluators, period
from app.qa.store import QaRepository
from app.xcally import XcallyError

ITEMS = [{"key": "greet", "label": "Greeting", "weight": 40}, {"key": "verify", "label": "Verify", "weight": 60}]


def recording(identity, agent, seconds):
    return {"id": identity, "UserId": agent, "duration": seconds * 1000, "queue": "GH_Q", "createdAt": "2026-11-05T09:00:00Z"}


class HelperTests(unittest.TestCase):
    def test_parse_evaluators(self):
        self.assertEqual(parse_evaluators('["Kofi", " Ama ", "Kofi", ""]'), ["Ama", "Kofi"])
        self.assertEqual(parse_evaluators("Ama\nKofi, Esi"), ["Ama", "Esi", "Kofi"])
        with self.assertRaises(ValueError):
            parse_evaluators("  \n ")

    def test_period_is_inclusive_local_days(self):
        start, end = period("2026-11-03", "2026-11-09")
        self.assertEqual((start.isoformat(), end.isoformat()), ("2026-11-03T00:00:00+00:00", "2026-11-10T00:00:00+00:00"))
        for bad in (("2026-11-09", "2026-11-03"), ("2026-10-01", "2026-11-09"), ("nope", "2026-11-09")):
            with self.assertRaises(ValueError):
                period(*bad)


class QaRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = QaRepository(Path(self.temp.name) / "qa.sqlite3")
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.executor.shutdown)
        router = create_router(settings=MagicMock(), executor=self.executor, repository=self.repo,
                               credentials=lambda user, password: ("user", "pass"))
        self.route = {(next(iter(r.methods)), r.path): r.endpoint for r in router.routes}

    def call(self, method, path):
        return self.route[(method, f"/api/qa{path}")]

    async def test_scorecard_create_list_archive(self):
        card = await self.call("POST", "/scorecards")(ScorecardIn(name="Calls v1", items=ITEMS))
        self.assertEqual((await self.call("GET", "/scorecards")())["scorecards"][0]["id"], card["id"])
        with self.assertRaises(HTTPException) as bad:
            await self.call("POST", "/scorecards")(ScorecardIn(name="Empty", items=[]))
        self.assertEqual(bad.exception.status_code, 422)
        await self.call("DELETE", "/scorecards/{scorecard_id}")(card["id"])
        self.assertEqual((await self.call("GET", "/scorecards")())["scorecards"], [])
        with self.assertRaises(HTTPException):
            await self.call("DELETE", "/scorecards/{scorecard_id}")("missing")

    async def test_plan_is_built_saved_and_skips_evaluated_recordings(self):
        card = self.repo.create_scorecard("Calls v1", ITEMS)
        self.repo.create_evaluation(recording_id=1, scorecard_id=card["id"], evaluator="Ama",
                                    answers={"greet": "5", "verify": "5"})
        client = MagicMock()
        client.voice_recordings.return_value = [recording(i, 7, s) for i, s in enumerate([30, 60, 150, 200, 400, 700], 1)]
        client.agents.return_value = [{"id": 7, "name": "kwame", "fullname": "Kwame Mensah"}]
        with patch.object(routes, "XcallyClient", return_value=client):
            plan = await self.call("POST", "/plans")(start="2026-11-03", end="2026-11-09", evaluators="Ama\nKofi",
                                                     campaign=" GH_Q ", market="gh", username=None, password=None)
        start, end = client.voice_recordings.call_args.args
        self.assertEqual(client.voice_recordings.call_args.kwargs, {"queue_name": "GH_Q"})
        self.assertEqual((start.isoformat()[:10], end.isoformat()[:10]), ("2026-11-03", "2026-11-10"))
        client.close.assert_called_once()
        agents = [a for entry in plan["assignments"] for a in entry["agents"]]
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["agent_name"], "Kwame Mensah")
        self.assertNotIn(1, [c["recording_id"] for c in agents[0]["calls"]])
        self.assertEqual(plan["skipped"]["already_evaluated"], 1)
        self.assertEqual((await self.call("GET", "/plans/{plan_id}")(plan["id"]))["seed"], plan["seed"])
        self.assertEqual((await self.call("GET", "/plans")())["plans"][0]["evaluators"], ["Ama", "Kofi"])

    async def test_plan_rejects_bad_input_and_reports_xcally_errors(self):
        with self.assertRaises(HTTPException) as bad:
            await self.call("POST", "/plans")(start="2026-11-09", end="2026-11-03", evaluators="Ama",
                                              campaign=None, market="GH", username=None, password=None)
        self.assertEqual(bad.exception.status_code, 422)
        client = MagicMock()
        client.voice_recordings.side_effect = XcallyError("No access.")
        with patch.object(routes, "XcallyClient", return_value=client):
            with self.assertRaises(HTTPException) as failed:
                await self.call("POST", "/plans")(start="2026-11-03", end="2026-11-09", evaluators="Ama",
                                                  campaign=None, market="GH", username=None, password=None)
        self.assertEqual((failed.exception.status_code, failed.exception.detail), (502, "No access."))
        client.close.assert_called_once()

    async def test_templates_are_served(self):
        templates = (await self.call("GET", "/scorecard-templates")())["templates"]
        self.assertEqual(set(templates), {"voice", "chat"})

    async def test_evaluation_save_and_list(self):
        card = self.repo.create_scorecard("Calls v1", ITEMS)
        saved = await self.call("POST", "/evaluations")(EvaluationIn(
            recording_id=9, scorecard_id=card["id"], evaluator="Ama", answers={"greet": "3", "verify": "5"}, agent_id=7))
        self.assertEqual((saved["percent"], saved["overall_fails"]), (80.0, 1))
        listed = await self.call("GET", "/evaluations")(agent_id=7, recording_id=None)
        self.assertEqual([e["id"] for e in listed["evaluations"]], [saved["id"]])
        with self.assertRaises(HTTPException) as bad:
            await self.call("POST", "/evaluations")(EvaluationIn(
                recording_id=9, scorecard_id=card["id"], evaluator="Ama", answers={"greet": "5"}))
        self.assertEqual(bad.exception.status_code, 422)

    async def test_audio_streams_and_closes_connections(self):
        upstream = MagicMock(headers={"Content-Type": "application/octet-stream"})
        upstream.iter_content.return_value = iter([b"ID3ab", b"cd"])
        client = MagicMock()
        client.recording_audio.return_value = upstream
        with patch.object(routes, "XcallyClient", return_value=client):
            response = await self.call("POST", "/recordings/{recording_id}/audio")(5, username=None, password=None)
        self.assertEqual(response.media_type, "audio/mpeg")
        self.assertEqual((response.headers["x-recording-format"], response.headers["x-recording-playable"]), ("MP3", "yes"))
        body = b"".join([chunk async for chunk in response.body_iterator])
        self.assertEqual(body, b"ID3abcd")
        upstream.close.assert_called_once()
        client.close.assert_called_once()

    async def test_gsm_audio_is_converted_to_wav(self):
        from tests.test_qa_audio import gsm_tone
        data = gsm_tone(1.0)
        upstream = MagicMock(headers={"Content-Type": "application/octet-stream",
                                      "Content-Disposition": 'attachment; filename="abc.gsm"'})
        upstream.iter_content.return_value = iter([data[:500], data[500:]])
        client = MagicMock()
        client.recording_audio.return_value = upstream
        with patch.object(routes, "XcallyClient", return_value=client):
            response = await self.call("POST", "/recordings/{recording_id}/audio")(5, username=None, password=None)
        self.assertEqual(response.media_type, "audio/wav")
        self.assertEqual(response.headers["x-recording-format"], "GSM")
        self.assertEqual(response.body[:4], b"RIFF")
        upstream.close.assert_called_once()
        client.close.assert_called_once()

    async def test_audio_rejects_json_and_closes_connections(self):
        upstream = MagicMock(headers={"Content-Type": "application/json"})
        upstream.iter_content.return_value = iter([b'{"message": "not found"}'])
        client = MagicMock()
        client.recording_audio.return_value = upstream
        with patch.object(routes, "XcallyClient", return_value=client):
            with self.assertRaises(HTTPException) as failed:
                await self.call("POST", "/recordings/{recording_id}/audio")(5, username=None, password=None)
        self.assertEqual(failed.exception.status_code, 502)
        self.assertIn("JSON instead of an audio file", failed.exception.detail)
        upstream.close.assert_called_once()
        client.close.assert_called_once()

    async def test_audio_failure_closes_client(self):
        client = MagicMock()
        client.recording_audio.side_effect = XcallyError("This recording file was not found in xCALLY.")
        with patch.object(routes, "XcallyClient", return_value=client):
            with self.assertRaises(HTTPException) as failed:
                await self.call("POST", "/recordings/{recording_id}/audio")(5, username=None, password=None)
        self.assertEqual(failed.exception.status_code, 502)
        client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
