import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.paths import PROJECT_DIR, data_directory


class DataDirectoryTests(unittest.TestCase):
    def test_source_run_keeps_existing_data_location(self):
        with patch.object(sys, "frozen", False, create=True):
            self.assertEqual(data_directory(), PROJECT_DIR)

    def test_packaged_run_creates_persistent_user_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys, "frozen", True, create=True), patch.dict(
                "os.environ", {"LOCALAPPDATA": directory}
            ):
                target = data_directory()
                self.assertEqual(target, Path(directory) / "XcallyUploads")
                (target / "history.txt").write_text("retained")
                self.assertEqual((data_directory() / "history.txt").read_text(), "retained")
