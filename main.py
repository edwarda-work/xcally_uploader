#!/usr/bin/env python3
"""FastAPI entry point for the xCALLY upload automation service."""

from __future__ import annotations

import asyncio
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.domain import MARKETS, FileResult, get_market
from app.history import JsonHistoryRepository
from app.service import process_file
from app.settings import Settings


PROJECT_DIR = Path(__file__).resolve().parent
settings = Settings.from_environment(PROJECT_DIR)
history_repository = JsonHistoryRepository(PROJECT_DIR / "upload_history.json", settings.history_limit)
executor = ThreadPoolExecutor(max_workers=settings.max_workers, thread_name_prefix="xcally-upload")

app = FastAPI(title="xCALLY Upload Automation", docs_url=None, redoc_url=None)


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    return FileResponse(PROJECT_DIR / "index.html")


@app.get("/api/status")
async def status() -> dict:
    return {
        "ready": bool(settings.username and settings.password),
        "user": settings.username or None,
        "session_supported": True,
        "markets": [
            {"code": config.code.value, "name": config.name, "prefix": config.list_prefix}
            for config in MARKETS.values()
        ],
    }


def resolve_credentials(username: Optional[str], password: Optional[str]) -> tuple[str, str]:
    return (username or settings.username).strip(), password if password is not None else settings.password


@app.post("/api/uploads")
@app.post("/api/upload", include_in_schema=False)
async def upload_files(
    files: list[UploadFile] = File(...),
    market: str = Form(...),
    username: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
) -> dict:
    try:
        market_config = get_market(market)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    resolved_username, resolved_password = resolve_credentials(username, password)
    if not resolved_username or not resolved_password:
        raise HTTPException(
            status_code=401,
            detail="Provide xCALLY credentials or configure them securely on the server.",
        )
    if not files:
        raise HTTPException(status_code=422, detail="Select at least one CSV file.")

    loop = asyncio.get_running_loop()

    async def handle_one(upload: UploadFile) -> FileResult:
        original_name = Path(upload.filename or "upload.csv").name
        if Path(original_name).suffix.lower() != ".csv":
            return FileResult(
                file_name=original_name, market=market_config.code.value, status="failed",
                message="Only CSV files are accepted.",
            )

        contents = await upload.read(settings.max_upload_bytes + 1)
        if len(contents) > settings.max_upload_bytes:
            return FileResult(
                file_name=original_name, market=market_config.code.value, status="failed",
                message=f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit.",
            )

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as temporary:
                temporary.write(contents)
                temporary_path = Path(temporary.name)
            return await loop.run_in_executor(
                executor,
                process_file,
                temporary_path,
                original_name,
                market_config,
                resolved_username,
                resolved_password,
                settings,
            )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    results = await asyncio.gather(*(handle_one(upload) for upload in files))
    history_repository.add(result.to_dict() for result in results)
    return {"results": [result.to_dict() for result in results]}


@app.get("/api/history")
async def get_history(market: Optional[str] = None) -> dict:
    history = history_repository.list()
    if market:
        try:
            market_code = get_market(market).code.value
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        history = [entry for entry in history if entry.get("market") == market_code]
    return {"history": history}


@app.delete("/api/history")
async def clear_history() -> dict:
    history_repository.clear()
    return {"ok": True}
