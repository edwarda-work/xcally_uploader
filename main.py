#!/usr/bin/env python3
"""FastAPI entry point for the xCALLY upload automation service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.domain import MARKETS, FileResult, get_market
from app.history import JsonHistoryRepository
from app.service import process_file
from app.segmentation import preview_segments
from app.settings import Settings
from app.paths import PROJECT_DIR, data_directory
from app.batches import BatchRepository
from app.campaigns import run_batch
from app.xcally import XcallyClient, XcallyError


DATA_DIR = data_directory()
settings = Settings.from_environment(DATA_DIR)
history_repository = JsonHistoryRepository(DATA_DIR / "upload_history.json", settings.history_limit)
executor = ThreadPoolExecutor(max_workers=settings.max_workers, thread_name_prefix="xcally-upload")
batch_repository = BatchRepository(DATA_DIR / "campaign_batches.sqlite3")
batch_slots = threading.BoundedSemaphore(settings.max_workers)


@asynccontextmanager
async def lifespan(app):
    batch_repository.interrupt_unfinished()
    yield
    await asyncio.to_thread(executor.shutdown, wait=True)


app = FastAPI(title="xCALLY Upload Automation", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    return FileResponse(PROJECT_DIR / "index.html")


@app.get("/campaigns.js", include_in_schema=False)
async def campaign_script() -> FileResponse:
    return FileResponse(PROJECT_DIR / "campaigns.js", media_type="text/javascript")


@app.get('/performance.js', include_in_schema=False)
async def performance_script() -> FileResponse:
    return FileResponse(PROJECT_DIR / 'performance.js', media_type='text/javascript')


@app.get('/agents.js', include_in_schema=False)
async def agents_script() -> FileResponse:
    return FileResponse(PROJECT_DIR / 'agents.js', media_type='text/javascript')


@app.get("/api/status")
async def status() -> dict:
    return {
        "ready": bool(settings.username and settings.password),
        "user": settings.username or None,
        "session_supported": True,
        "max_upload_bytes": settings.max_upload_bytes,
        "markets": [
            {"code": config.code.value, "name": config.name, "prefix": config.list_prefix}
            for config in MARKETS.values()
        ],
    }


def resolve_credentials(username: Optional[str], password: Optional[str]) -> tuple[str, str]:
    return (username or settings.username).strip(), password if password is not None else settings.password


def campaign_credentials(username, password):
    credentials = resolve_credentials(username, password)
    if not all(credentials):
        raise HTTPException(status_code=401, detail="Enter xCALLY credentials or configure them on the server.")
    return credentials


@app.post("/api/campaigns/options")
async def campaign_options(username: Optional[str] = Form(None), password: Optional[str] = Form(None)) -> dict:
    credentials = campaign_credentials(username, password)

    def fetch_options():
        client = XcallyClient(settings, *credentials)
        try:
            return {"workflow": "replace_campaign_lists_v1", "agents": client.agents(), "campaigns": client.campaigns()}
        finally:
            client.close()

    try:
        return await asyncio.get_running_loop().run_in_executor(executor, fetch_options)
    except XcallyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post('/api/performance/campaigns')
async def performance_campaigns(username: Optional[str] = Form(None), password: Optional[str] = Form(None)) -> dict:
    credentials = campaign_credentials(username, password)

    def fetch():
        client = XcallyClient(settings, *credentials)
        try:
            return {'campaigns': sorted(client.campaigns(active_only=True), key=lambda row: row['name'].casefold())}
        finally:
            client.close()

    try:
        return await asyncio.get_running_loop().run_in_executor(executor, fetch)
    except XcallyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get('/campaign-controls.js', include_in_schema=False)
async def campaign_controls_script() -> FileResponse:
    return FileResponse(PROJECT_DIR / 'campaign-controls.js', media_type='text/javascript')


@app.post('/api/campaigns/active')
async def change_campaign_active(
    campaign_ids: str = Form(...), active: bool = Form(...),
    username: Optional[str] = Form(None), password: Optional[str] = Form(None),
) -> dict:
    credentials = campaign_credentials(username, password)
    try:
        ids = json.loads(campaign_ids)
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 100
                or any(type(value) is not int or value <= 0 for value in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('Choose between 1 and 100 distinct campaign IDs.')
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def change():
        operation_id = str(uuid.uuid4())
        try:
            batch_repository.lock_campaigns(operation_id, ids)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail='A selected campaign is being updated. Wait for it to finish.') from exc
        client = None
        results = []
        try:
            client = XcallyClient(settings, *credentials)
            for campaign_id in ids:
                try:
                    queue = client.queue(campaign_id)
                    if queue.get('type') != 'outbound':
                        raise XcallyError('Only outbound campaigns can be controlled here.')
                    if queue.get('dialActive') != active:
                        client.set_campaign_active(campaign_id, active)
                    results.append({'id': campaign_id, 'active': active, 'ok': True})
                except XcallyError as exc:
                    results.append({'id': campaign_id, 'active': None, 'ok': False, 'error': str(exc)})
            return {'results': results}
        finally:
            try:
                if client is not None:
                    client.close()
            finally:
                batch_repository.unlock_campaigns(operation_id)

    return await asyncio.get_running_loop().run_in_executor(executor, change)


@app.post('/api/performance/campaigns/{campaign_id}')
async def performance_campaign(campaign_id: int, username: Optional[str] = Form(None), password: Optional[str] = Form(None)) -> dict:
    if campaign_id <= 0:
        raise HTTPException(status_code=422, detail='Invalid campaign ID.')
    credentials = campaign_credentials(username, password)

    def fetch():
        client = XcallyClient(settings, *credentials)
        try:
            return client.campaign_monitor_snapshot(campaign_id)
        finally:
            client.close()

    try:
        return await asyncio.get_running_loop().run_in_executor(executor, fetch)
    except XcallyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post('/api/performance/campaigns/{campaign_id}/agents')
async def performance_campaign_agents(campaign_id: int, username: Optional[str] = Form(None), password: Optional[str] = Form(None)) -> dict:
    if campaign_id <= 0:
        raise HTTPException(status_code=422, detail='Invalid campaign ID.')
    credentials = campaign_credentials(username, password)

    def fetch():
        client = XcallyClient(settings, *credentials)
        try:
            return client.campaign_agent_status(campaign_id)
        finally:
            client.close()

    try:
        return await asyncio.get_running_loop().run_in_executor(executor, fetch)
    except XcallyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post('/api/performance/campaigns/{campaign_id}/hourly-calls')
async def performance_hourly_calls(campaign_id: int, username: Optional[str] = Form(None), password: Optional[str] = Form(None)) -> dict:
    if campaign_id <= 0:
        raise HTTPException(status_code=422, detail='Invalid campaign ID.')
    credentials = campaign_credentials(username, password)

    def fetch():
        client = XcallyClient(settings, *credentials)
        try:
            return client.campaign_hourly_answered(campaign_id)
        finally:
            client.close()

    try:
        return await asyncio.get_running_loop().run_in_executor(executor, fetch)
    except XcallyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post('/api/performance/campaigns/{campaign_id}/hourly-calls/stream')
async def performance_hourly_calls_stream(campaign_id: int, username: Optional[str] = Form(None), password: Optional[str] = Form(None)):
    if campaign_id <= 0:
        raise HTTPException(status_code=422, detail='Invalid campaign ID.')
    credentials = campaign_credentials(username, password)

    def chunks():
        client = XcallyClient(settings, *credentials)
        try:
            for snapshot in client.iter_campaign_hourly_answered(campaign_id):
                yield json.dumps(snapshot, separators=(',', ':')) + '\n'
        except XcallyError as exc:
            yield json.dumps({'error': str(exc)}) + '\n'
        finally:
            client.close()

    return StreamingResponse(chunks(), media_type='application/x-ndjson', headers={'Cache-Control': 'no-store'})


@app.post("/api/campaign-batches", status_code=202)
async def create_campaign_batch(
    file: UploadFile = File(...), market: str = Form(...), agent_column: str = Form(...),
    assignments: str = Form(...), batch_id: str = Form(...),
    base_name: str = Form("Contacts"), username: Optional[str] = Form(None), password: Optional[str] = Form(None),
) -> dict:
    try:
        credentials = campaign_credentials(username, password)
        market_config = get_market(market)
        batch_id = str(uuid.UUID(batch_id))
        if len(base_name) > 50:
            raise ValueError("Use a base name of at most 50 characters.")
        parsed = json.loads(assignments)
        if not isinstance(parsed, dict) or not parsed or any(type(v) is not int or v <= 0 for v in parsed.values()):
            raise ValueError("Provide an xCALLY agent ID for every segment.")
        filename = Path(file.filename or "").name
        if Path(filename).suffix.lower() != '.csv':
            raise ValueError('Only CSV files are accepted.')
        contents = await file.read(settings.max_upload_bytes + 1)
        if len(contents) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail='File exceeds the configured upload size limit.')
        fingerprint = hashlib.sha256(contents + json.dumps(
            [market_config.code.value, agent_column, parsed, base_name, credentials[0], filename],
            sort_keys=True).encode()).hexdigest()
        batch = {'id': batch_id, 'status': 'queued', 'file_name': filename, 'results': [], 'message': 'Checking agents and existing campaigns.'}
        if not batch_slots.acquire(blocking=False):
            existing = batch_repository.get(batch_id)
            if existing:
                stored, _ = batch_repository.create(batch, fingerprint)
                return stored
            raise HTTPException(status_code=429, detail='All campaign workers are busy. Try again shortly.')
        try:
            batch, created = batch_repository.create(batch, fingerprint)
            if not created:
                batch_slots.release()
                return batch

            def work():
                try:
                    run_batch(batch, contents, agent_column, parsed, base_name.strip() or 'Contacts',
                              market_config, settings, *credentials, batch_repository, history_repository)
                finally:
                    batch_slots.release()

            executor.submit(work)
        except Exception:
            batch_slots.release()
            raise
        return {'id': batch_id, 'status': 'queued', 'results': [], 'message': 'Campaign batch submitted.'}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()


@app.get("/api/campaign-batches/{batch_id}")
async def campaign_batch_status(batch_id: str) -> dict:
    batch = batch_repository.get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail='Campaign batch not found.')
    return batch


@app.post("/api/segments/preview")
async def preview_agent_segments(
    file: UploadFile = File(...), agent_column: Optional[str] = Form(None),
) -> dict:
    try:
        if Path(file.filename or "").suffix.lower() != ".csv":
            raise HTTPException(status_code=422, detail="Only CSV files are accepted.")
        contents = await file.read(settings.max_upload_bytes + 1)
        if len(contents) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="File exceeds the configured upload size limit.")
        return await asyncio.get_running_loop().run_in_executor(executor, preview_segments, contents, agent_column)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()


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
