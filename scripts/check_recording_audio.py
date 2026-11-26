"""Read-only check: what does xCALLY send when a recording is downloaded?

Usage (from this folder):
    .venv/bin/python scripts/check_recording_audio.py            # 5 most recent recordings
    .venv/bin/python scripts/check_recording_audio.py 2441852    # specific recording IDs

Prompts for xCALLY credentials. Nothing is changed in xCALLY and no audio is saved:
only the response headers and the first bytes of each file are inspected.
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.paths import data_directory  # noqa: E402
from app.qa.audio import identify  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.xcally import XcallyClient  # noqa: E402

HEAD_BYTES = 4096


def main() -> None:
    ids = [int(value) for value in sys.argv[1:]]
    username = input("xCALLY username: ").strip()
    password = getpass.getpass("xCALLY password (hidden): ")
    client = XcallyClient(Settings.from_environment(data_directory()), username, password)
    try:
        if not ids:
            data = client.json_request("GET", "/voice/recordings", params={
                "fields": "id,duration,format,createdAt", "sort": "-id", "limit": 5})
            rows = client.rows(data)
            ids = [row["id"] for row in rows]
            print("Recent recordings:", ", ".join(f"{r['id']} (format={r.get('format')!r}, duration={r.get('duration')})" for r in rows))
        for recording_id in ids:
            print(f"\nRecording {recording_id}")
            response = client.request("GET", f"/voice/recordings/{recording_id}/download", stream=True)
            try:
                print(f"  HTTP {response.status_code}")
                for name in ("Content-Type", "Content-Length", "Content-Disposition", "Content-Encoding", "Location"):
                    if response.headers.get(name):
                        print(f"  {name}: {response.headers[name]}")
                head = b""
                for chunk in response.iter_content(chunk_size=HEAD_BYTES):
                    head = chunk[:HEAD_BYTES]
                    break
                print(f"  First bytes (hex): {head[:16].hex(' ')}")
                printable = "".join(chr(b) if 32 <= b < 127 else "." for b in head[:48])
                print(f"  First bytes (text): {printable}")
                print(f"  Detected: {identify(head, response.headers.get('Content-Type'))}")
            finally:
                response.close()
    finally:
        client.close()


if __name__ == "__main__":
    main()
