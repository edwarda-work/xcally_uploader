from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterable


class JsonHistoryRepository:
    """Small single-instance repository with locked, atomic writes."""

    def __init__(self, path: Path, limit: int = 500):
        self.path = path
        self.limit = limit
        self._lock = threading.Lock()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_unlocked()

    def add(self, entries: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            history = self._read_unlocked()
            for entry in entries:
                history.insert(0, entry)
            self._write_unlocked(history[: self.limit])

    def clear(self) -> None:
        with self._lock:
            self._write_unlocked([])

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text())
            return value if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write_unlocked(self, value: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(json.dumps(value, indent=2))
        temporary.replace(self.path)
