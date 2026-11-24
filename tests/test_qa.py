import tempfile
import unittest
from pathlib import Path

from app.qa.scoring import comment_note, score, validate_items
from app.qa.store import QaRepository

from app.qa.scoring import TEMPLATES

ITEMS = [
    {"key": "greeting", "label": "Greeting and identification", "weight": 20},
    {"key": "verify", "label": "Verified customer identity", "weight": 30, "auto_fail": True},
    {"key": "offer", "label": "Presented repayment options", "weight": 50},
]
ALL_FIVE = {"greeting": "5", "verify": "5", "offer": "5"}


class ScoringTests(unittest.TestCase):
    def test_full_and_half_weight(self):
        self.assertEqual(score(validate_items(ITEMS), ALL_FIVE),
                         {"percent": 100.0, "raw_percent": 100.0, "critical_failed": False,
                          "auto_failed": [], "overall_fails": 0})
        result = score(validate_items(ITEMS), {"greeting": "3", "verify": "5", "offer": "3"})
        self.assertEqual((result["percent"], result["overall_fails"]), (65.0, 2))

    def test_na_is_left_out_of_score_and_fails(self):
        result = score(validate_items(ITEMS), {"greeting": "na", "verify": "5", "offer": "3"})
        self.assertEqual((result["percent"], result["overall_fails"]), (68.8, 1))  # (30 + 25) / 80
        self.assertIsNone(score(validate_items(ITEMS), dict.fromkeys(ALL_FIVE, "na"))["percent"])

    def test_critical_fail_sets_total_to_zero_but_keeps_raw_score(self):
        result = score(validate_items(ITEMS), {"greeting": "3", "verify": "5", "offer": "5"}, critical_fail=True)
        self.assertEqual((result["percent"], result["raw_percent"], result["critical_failed"]), (0.0, 90.0, True))

    def test_grade_one_earns_a_fifth(self):
        result = score(validate_items(ITEMS), {"greeting": "1", "verify": "5", "offer": "5"})
        self.assertEqual((result["percent"], result["critical_failed"], result["overall_fails"]), (84.0, False, 1))

    def test_auto_fail_metric_graded_one_fails_the_call(self):
        result = score(validate_items(ITEMS), {"greeting": "5", "verify": "1", "offer": "5"})
        self.assertEqual((result["percent"], result["raw_percent"]), (0.0, 76.0))
        self.assertEqual((result["critical_failed"], result["auto_failed"]), (True, ["verify"]))

    def test_voice_example(self):
        answers = {"accuracy_compliance": "1", "experience_empathy": "5", "ownership_resolution": "5",
                   "communication": "5", "business_risk": "5"}
        self.assertEqual(score(validate_items(TEMPLATES["voice"]["items"]), answers)["percent"], 76.0)

    def test_templates_match_the_sheet(self):
        for kind in ("voice", "chat"):
            items = validate_items(TEMPLATES[kind]["items"])
            self.assertEqual(sum(item["weight"] for item in items), 100)
        self.assertEqual([i["key"] for i in validate_items(TEMPLATES["chat"]["items"]) if i["auto_fail"]],
                         ["accuracy_compliance", "resolution_ownership", "personalisation_tone"])
        self.assertFalse(any(i["auto_fail"] for i in validate_items(TEMPLATES["voice"]["items"])))

    def test_legacy_critical_flag_reads_as_auto_fail(self):
        self.assertTrue(validate_items([{"key": "a", "label": "A", "critical": True}])[0]["auto_fail"])

    def test_rejects_missing_unknown_and_invalid_answers(self):
        items = validate_items(ITEMS)
        with self.assertRaises(ValueError):
            score(items, {"greeting": "5", "verify": "5"})
        with self.assertRaises(ValueError):
            score(items, {**ALL_FIVE, "extra": "5"})
        for bad in ("pass", "4", "0", 5):
            with self.assertRaises(ValueError):
                score(items, {**ALL_FIVE, "greeting": bad})

    def test_rejects_bad_items(self):
        with self.assertRaises(ValueError):
            validate_items([])
        with self.assertRaises(ValueError):
            validate_items([{"key": "a", "label": "A"}, {"key": "a", "label": "B"}])
        with self.assertRaises(ValueError):
            validate_items([{"key": "a", "label": "A", "weight": 0}])

    def test_comment_note(self):
        self.assertEqual(comment_note("Collections v1", 66.7, True, " Rushed "),
                         "QA Collections v1: 66.7% | CRITICAL FAIL | Rushed")


class QaRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = QaRepository(Path(self.directory.name) / "qa.sqlite3")

    def tearDown(self):
        self.directory.cleanup()

    def test_evaluation_snapshots_scorecard_and_tracks_sync(self):
        card = self.repo.create_scorecard("Collections v1", ITEMS, market="GH")
        evaluation = self.repo.create_evaluation(
            recording_id=42, scorecard_id=card["id"], evaluator="qa.lead",
            answers={"greeting": "5", "verify": "5", "offer": "3"}, agent_id=7, campaign="GH_AGENT_QUEUE_X")
        self.assertEqual((evaluation["percent"], evaluation["overall_fails"]), (75.0, 1))
        self.assertEqual(evaluation["sync_status"], "not_synced")
        self.assertEqual(evaluation["items"], card["items"])

        self.repo.mark_sync_failed(evaluation["id"], "HTTP 500")
        self.assertEqual(self.repo.list_evaluations(sync_status="failed")[0]["sync_error"], "HTTP 500")
        self.repo.mark_synced(evaluation["id"], 3)
        synced = self.repo.get_evaluation(evaluation["id"])
        self.assertEqual((synced["sync_status"], synced["synced_rating"], synced["sync_error"]), ("synced", 3, None))
        self.assertEqual(len(self.repo.list_evaluations(agent_id=7)), 1)

    def test_archived_scorecard_cannot_be_used(self):
        card = self.repo.create_scorecard("Old", ITEMS)
        self.repo.archive_scorecard(card["id"])
        self.assertEqual(self.repo.list_scorecards(), [])
        with self.assertRaises(ValueError):
            self.repo.create_evaluation(recording_id=1, scorecard_id=card["id"], evaluator="x",
                                        answers=ALL_FIVE)

    def test_critical_fail_needs_a_reason_and_is_stored(self):
        card = self.repo.create_scorecard("Voice", ITEMS)
        with self.assertRaisesRegex(ValueError, "reason"):
            self.repo.create_evaluation(recording_id=1, scorecard_id=card["id"], evaluator="x",
                                        answers=ALL_FIVE, critical_fail=True)
        saved = self.repo.create_evaluation(recording_id=1, scorecard_id=card["id"], evaluator="x",
                                            answers=ALL_FIVE, critical_fail=True, critical_reason=" Fraud risk ")
        self.assertEqual((saved["percent"], saved["raw_percent"], saved["critical_failed"], saved["critical_reason"]),
                         (0.0, 100.0, True, "Fraud risk"))

    def test_old_database_gets_new_columns(self):
        import sqlite3
        path = Path(self.directory.name) / "old.sqlite3"
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        with db:
            db.execute("""CREATE TABLE evaluations (id TEXT PRIMARY KEY, recording_id INTEGER NOT NULL,
                scorecard_id TEXT NOT NULL, scorecard_name TEXT NOT NULL, items TEXT NOT NULL,
                agent_id INTEGER, campaign TEXT, evaluator TEXT NOT NULL, answers TEXT NOT NULL, comment TEXT NOT NULL,
                percent REAL, critical_failed INTEGER NOT NULL, sync_status TEXT NOT NULL, synced_rating INTEGER,
                sync_error TEXT, synced_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        repo = QaRepository(path)
        card = repo.create_scorecard("Voice", ITEMS)
        self.assertEqual(repo.create_evaluation(recording_id=2, scorecard_id=card["id"], evaluator="x",
                                                answers=ALL_FIVE)["overall_fails"], 0)


if __name__ == "__main__":
    unittest.main()
