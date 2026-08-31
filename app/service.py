from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .domain import FileResult, MarketConfig
from .settings import Settings
from .xcally import XcallyClient, build_binding, read_headers


def make_list_name(market: MarketConfig, original_name: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    stem = Path(original_name).stem.strip() or "upload"
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "upload"
    return f"{market.list_prefix}_{now:%Y%m%d}_{safe_stem}"[:100]


def process_file(
    csv_path: Path,
    original_name: str,
    market: MarketConfig,
    username: str,
    password: str,
    settings: Settings,
) -> FileResult:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    list_name = make_list_name(market, original_name)
    try:
        if csv_path.stat().st_size > settings.max_upload_bytes:
            raise ValueError(f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit.")
        headers = read_headers(csv_path)
        client = XcallyClient(settings, username, password)
        created = client.create_list(list_name)
        list_id = int(created["id"])
        binding, skipped = build_binding(headers, client.fetch_list_fields(list_id), market)
        staged = client.stage_upload(csv_path, original_name)
        staged_id = staged.get("file", {}).get("name") if isinstance(staged.get("file"), dict) else None
        if not staged_id:
            raise RuntimeError("xCALLY did not return a staged file identifier.")
        final = client.finalize_upload(list_id, staged_id, binding)
        pid = final.get("pid")
        return FileResult(
            file_name=original_name, market=market.code.value, status="success",
            message=f"Uploaded successfully into list {list_id}.", timestamp=timestamp,
            list_name=list_name, list_id=list_id,
            pid=int(pid) if isinstance(pid, int) else None, skipped_headers=skipped or None,
        )
    except Exception as exc:
        return FileResult(
            file_name=original_name, market=market.code.value, status="failed",
            message=str(exc), timestamp=timestamp, list_name=list_name,
        )
