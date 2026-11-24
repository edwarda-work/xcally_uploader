"""Local QA storage: scorecard templates and evaluations, with xCALLY sync state.

Kept in its own SQLite file so clearing Collections history never touches QA data.
Each evaluation stores a snapshot of the scorecard items it was scored against,
so editing a scorecard later never changes past results.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.qa.scoring import comment_note, score, to_xcally_rating, validate_items

SYNC_STATES = {"not_synced", "synced", "failed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class QaRepository:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS scorecards (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, market TEXT,
                items TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS evaluations (
                id TEXT PRIMARY KEY, recording_id INTEGER NOT NULL,
                scorecard_id TEXT NOT NULL, scorecard_name TEXT NOT NULL, items TEXT NOT NULL,
                agent_id INTEGER, campaign TEXT, evaluator TEXT NOT NULL,
                answers TEXT NOT NULL, comment TEXT NOT NULL,
                percent REAL, critical_failed INTEGER NOT NULL,
                sync_status TEXT NOT NULL, synced_rating INTEGER, sync_error TEXT, synced_at TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            # Added with the 5/3/1 scorecard engine; older databases get the columns here.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(evaluations)")}
            for column, kind in (("raw_percent", "REAL"), ("overall_fails", "INTEGER"), ("critical_reason", "TEXT")):
                if column not in columns:
                    db.execute(f"ALTER TABLE evaluations ADD COLUMN {column} {kind}")
            db.execute("CREATE INDEX IF NOT EXISTS evaluations_recording ON evaluations(recording_id)")
            db.execute("CREATE INDEX IF NOT EXISTS evaluations_agent ON evaluations(agent_id)")
            db.execute("""CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, period_start TEXT NOT NULL,
                period_end TEXT NOT NULL, campaign TEXT, data TEXT NOT NULL)""")

    # Sampling plans

    def save_plan(self, plan: dict, *, period_start: str, period_end: str, campaign: str | None) -> dict:
        record = {"id": str(uuid.uuid4()), "created_at": _now(), "period_start": period_start,
                  "period_end": period_end, "campaign": campaign, **plan}
        with self.connect() as db:
            db.execute("INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?)",
                       (record["id"], record["created_at"], period_start, period_end, campaign, json.dumps(plan)))
        return record

    def get_plan(self, plan_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        return {**{k: data[k] for k in ("id", "created_at", "period_start", "period_end", "campaign")},
                **json.loads(data["data"])}

    def list_plans(self, limit: int = 20) -> list[dict]:
        """Newest first, without the call lists."""
        with self.connect() as db:
            rows = db.execute("SELECT * FROM plans ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            plan = json.loads(row["data"])
            result.append({"id": row["id"], "created_at": row["created_at"], "period_start": row["period_start"],
                           "period_end": row["period_end"], "campaign": row["campaign"],
                           "market": plan.get("thresholds", {}).get("campaign_prefix"),
                           "evaluators": [entry["evaluator"] for entry in plan["assignments"]],
                           "agents": sum(len(entry["agents"]) for entry in plan["assignments"])})
        return result

    def evaluated_recording_ids(self) -> set[int]:
        with self.connect() as db:
            return {row[0] for row in db.execute("SELECT DISTINCT recording_id FROM evaluations").fetchall()}

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    # Scorecards

    def create_scorecard(self, name: str, items: list[dict], market: str | None = None) -> dict:
        if not name.strip():
            raise ValueError("A scorecard needs a name.")
        now = _now()
        scorecard_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO scorecards VALUES (?, ?, ?, ?, 0, ?, ?)",
                       (scorecard_id, name.strip(), market, json.dumps(validate_items(items)), now, now))
        return self.get_scorecard(scorecard_id)

    def get_scorecard(self, scorecard_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM scorecards WHERE id=?", (scorecard_id,)).fetchone()
        return self._scorecard(row) if row else None

    def list_scorecards(self, include_archived: bool = False) -> list[dict]:
        query = "SELECT * FROM scorecards" + ("" if include_archived else " WHERE archived=0") + " ORDER BY name"
        with self.connect() as db:
            return [self._scorecard(row) for row in db.execute(query).fetchall()]

    def archive_scorecard(self, scorecard_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE scorecards SET archived=1, updated_at=? WHERE id=?", (_now(), scorecard_id))

    @staticmethod
    def _scorecard(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["items"] = json.loads(data["items"])
        data["archived"] = bool(data["archived"])
        return data

    # Evaluations

    def create_evaluation(self, *, recording_id: int, scorecard_id: str, evaluator: str,
                          answers: dict[str, str], comment: str = "",
                          agent_id: int | None = None, campaign: str | None = None,
                          critical_fail: bool = False, critical_reason: str = "") -> dict:
        scorecard = self.get_scorecard(scorecard_id)
        if not scorecard or scorecard["archived"]:
            raise ValueError("Choose an active scorecard.")
        if not evaluator.strip():
            raise ValueError("An evaluation needs an evaluator name.")
        if critical_fail and not critical_reason.strip():
            raise ValueError("Give a reason for the critical fail.")
        items = validate_items(scorecard["items"])
        result = score(items, answers, critical_fail)
        now = _now()
        evaluation_id = str(uuid.uuid4())
        row = {
            "id": evaluation_id, "recording_id": recording_id, "scorecard_id": scorecard["id"],
            "scorecard_name": scorecard["name"], "items": json.dumps(items), "agent_id": agent_id,
            "campaign": campaign, "evaluator": evaluator.strip(), "answers": json.dumps(answers),
            "comment": comment, "percent": result["percent"], "critical_failed": int(result["critical_failed"]),
            "sync_status": "not_synced", "created_at": now, "updated_at": now,
            "raw_percent": result["raw_percent"], "overall_fails": result["overall_fails"],
            "critical_reason": critical_reason.strip() if critical_fail else "",
        }
        with self.connect() as db:
            db.execute(f"INSERT INTO evaluations ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                       tuple(row.values()))
        return self.get_evaluation(evaluation_id)

    def get_evaluation(self, evaluation_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM evaluations WHERE id=?", (evaluation_id,)).fetchone()
        return self._evaluation(row) if row else None

    def list_evaluations(self, *, agent_id: int | None = None, recording_id: int | None = None,
                         sync_status: str | None = None) -> list[dict]:
        clauses, params = [], []
        for column, value in (("agent_id", agent_id), ("recording_id", recording_id), ("sync_status", sync_status)):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        query = "SELECT * FROM evaluations" + (f" WHERE {' AND '.join(clauses)}" if clauses else "") + " ORDER BY created_at DESC"
        with self.connect() as db:
            return [self._evaluation(row) for row in db.execute(query, params).fetchall()]

    def sync_payload(self, evaluation: dict) -> dict:
        """Fields to PUT on /voice/recordings/{recording_id}."""
        return {
            "rating": to_xcally_rating(evaluation["percent"], evaluation["critical_failed"]),
            "commentNote": comment_note(evaluation["scorecard_name"], evaluation["percent"],
                                        evaluation["critical_failed"], evaluation["comment"]),
        }

    def mark_synced(self, evaluation_id: str, rating: int | None) -> None:
        now = _now()
        with self.connect() as db:
            db.execute("""UPDATE evaluations SET sync_status='synced', synced_rating=?, sync_error=NULL,
                          synced_at=?, updated_at=? WHERE id=?""", (rating, now, now, evaluation_id))

    def mark_sync_failed(self, evaluation_id: str, error: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE evaluations SET sync_status='failed', sync_error=?, updated_at=? WHERE id=?",
                       (error, _now(), evaluation_id))

    @staticmethod
    def _evaluation(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["items"] = json.loads(data["items"])
        data["answers"] = json.loads(data["answers"])
        data["critical_failed"] = bool(data["critical_failed"])
        return data
