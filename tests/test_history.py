import tempfile
import unittest
from pathlib import Path

from app.history import JsonHistoryRepository


class HistoryRepositoryTests(unittest.TestCase):
    def test_add_limit_and_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = JsonHistoryRepository(Path(directory) / "history.json", limit=2)
            repository.add([{"id": 1}, {"id": 2}, {"id": 3}])
            self.assertEqual(repository.list(), [{"id": 3}, {"id": 2}])
            repository.clear()
            self.assertEqual(repository.list(), [])


if __name__ == "__main__":
    unittest.main()
