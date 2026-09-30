"""Durable batch results and idempotent submission (no credentials or CSV rows)."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class BatchRepository:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS campaign_locks (campaign_id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, batch: dict, fingerprint: str) -> tuple[dict, bool]:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO batches VALUES (?, ?, ?)",
                       (batch['id'], fingerprint, json.dumps(batch)))
            created = db.execute("SELECT changes()").fetchone()[0] == 1
            stored_fingerprint, data = db.execute("SELECT fingerprint, data FROM batches WHERE id=?", (batch['id'],)).fetchone()
            if stored_fingerprint != fingerprint:
                raise ValueError("This submission ID belongs to a different request. Start a new batch.")
            return json.loads(data), created

    def get(self, batch_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM batches WHERE id=?", (batch_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, batch: dict) -> None:
        with self.connect() as db:
            db.execute("UPDATE batches SET data=? WHERE id=?", (json.dumps(batch), batch['id']))

    def lock_campaigns(self, batch_id: str, campaign_ids: list[int]) -> None:
        try:
            with self.connect() as db:
                for campaign_id in campaign_ids:
                    db.execute('INSERT INTO campaign_locks VALUES (?, ?)', (campaign_id, batch_id))
        except sqlite3.IntegrityError as exc:
            raise ValueError('One of these campaigns is already being updated by another batch. Wait for that batch to finish.') from exc

    def unlock_campaigns(self, batch_id: str) -> None:
        with self.connect() as db:
            db.execute('DELETE FROM campaign_locks WHERE batch_id=?', (batch_id,))

    def interrupt_unfinished(self) -> None:
        with self.connect() as db:
            db.execute('DELETE FROM campaign_locks')
            for batch_id, data in db.execute("SELECT id, data FROM batches").fetchall():
                batch = json.loads(data)
                if batch['status'] in {'queued', 'running'}:
                    batch['status'] = 'interrupted'
                    batch['message'] = 'Server restarted. Review the recorded resource IDs in xCALLY before creating another batch.'
                    for result in batch.get('results', []):
                        if result['status'] in {'pending', 'running'}:
                            result['status'] = 'interrupted'
                            result['message'] = 'Server restarted. This step was not confirmed; review any resources in xCALLY.'
                    db.execute("UPDATE batches SET data=? WHERE id=?", (json.dumps(batch), batch_id))
