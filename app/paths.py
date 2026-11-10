"""Separate bundled read-only assets from persistent desktop user data."""
import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent


def data_directory() -> Path:
    if getattr(sys, "frozen", False):
        path = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "XcallyUploads"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return PROJECT_DIR
