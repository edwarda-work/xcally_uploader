"""HTTP routes for the Quality Assurance workspace, mounted under /api/qa.

Built by a factory so this module never imports main.py or Collections code.
xCALLY access is read only here: listing recordings, agents, and audio.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.qa.audio import MAX_GSM_BYTES, filename_from_disposition, gsm_to_wav, identify
from app.domain import get_market
from app.qa.sampling import build_plan
from app.qa.scoring import TEMPLATES
from app.qa.store import QaRepository
from app.xcally import XcallyClient, XcallyError

LOCAL_ZONE = ZoneInfo("Africa/Accra")
MAX_PLAN_DAYS = 31
MAX_EVALUATORS = 50


class ScorecardIn(BaseModel):
    name: str
    market: Optional[str] = None
    items: list[dict] = Field(default_factory=list)


class EvaluationIn(BaseModel):
    recording_id: int
    scorecard_id: str
    evaluator: str
    answers: dict[str, str]
    comment: str = ""
    agent_id: Optional[int] = None
    campaign: Optional[str] = None
    critical_fail: bool = False
    critical_reason: str = ""


def parse_evaluators(raw: str) -> list[str]:
    """Accept a JSON list or one name per line / comma."""
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw.replace(",", "\n").splitlines()
    if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
        raise ValueError("List QA evaluators as names.")
    names = sorted({name.strip() for name in value if name.strip()})
    if not 1 <= len(names) <= MAX_EVALUATORS:
        raise ValueError(f"Add between 1 and {MAX_EVALUATORS} QA evaluators.")
    return names


def period(start: str, end: str) -> tuple[datetime, datetime]:
    """Inclusive local dates -> [start midnight, day after end midnight) in Africa/Accra."""
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("Dates must be YYYY-MM-DD.") from exc
    if last < first:
        raise ValueError("The end date must be on or after the start date.")
    if (last - first).days + 1 > MAX_PLAN_DAYS:
        raise ValueError(f"Choose a period of at most {MAX_PLAN_DAYS} days.")
    return (datetime.combine(first, time(), LOCAL_ZONE),
            datetime.combine(last + timedelta(days=1), time(), LOCAL_ZONE))


def create_router(*, settings, executor, repository: QaRepository,
                  credentials: Callable[[Optional[str], Optional[str]], tuple[str, str]]) -> APIRouter:
    router = APIRouter(prefix="/api/qa", tags=["qa"])

    async def in_executor(fn):
        try:
            return await asyncio.get_running_loop().run_in_executor(executor, fn)
        except XcallyError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Scorecards (local only)

    @router.get("/scorecard-templates")
    async def scorecard_templates() -> dict:
        return {"templates": TEMPLATES}

    @router.get("/scorecards")
    async def list_scorecards() -> dict:
        return {"scorecards": repository.list_scorecards()}

    @router.post("/scorecards", status_code=201)
    async def create_scorecard(body: ScorecardIn) -> dict:
        try:
            return repository.create_scorecard(body.name, body.items, market=body.market)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete("/scorecards/{scorecard_id}")
    async def archive_scorecard(scorecard_id: str) -> dict:
        if not repository.get_scorecard(scorecard_id):
            raise HTTPException(status_code=404, detail="Scorecard not found.")
        repository.archive_scorecard(scorecard_id)
        return {"archived": scorecard_id}

    # Sampling plans

    @router.post("/plans", status_code=201)
    async def create_plan(start: str = Form(...), end: str = Form(...), evaluators: str = Form(...),
                          campaign: Optional[str] = Form(None), market: str = Form("GH"),
                          username: Optional[str] = Form(None), password: Optional[str] = Form(None)) -> dict:
        try:
            names = parse_evaluators(evaluators)
            market_code = get_market(market).code.value
            window_start, window_end = period(start, end)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        campaign = (campaign or "").strip() or None
        creds = credentials(username, password)
        excluded = repository.evaluated_recording_ids()

        def fetch():
            client = XcallyClient(settings, *creds)
            try:
                recordings = client.voice_recordings(window_start, window_end, queue_name=campaign)
                agent_names = {row["id"]: row.get("fullname") or row.get("name") for row in client.agents()}
            finally:
                client.close()
            return build_plan(recordings, names, excluded_recording_ids=excluded, agent_names=agent_names,
                              campaign_prefix=market_code)

        plan = await in_executor(fetch)
        return repository.save_plan(plan, period_start=start, period_end=end, campaign=campaign)

    @router.get("/plans")
    async def list_plans() -> dict:
        return {"plans": repository.list_plans()}

    @router.get("/plans/{plan_id}")
    async def get_plan(plan_id: str) -> dict:
        plan = repository.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="Sampling plan not found.")
        return plan

    # Recording audio (POST so credentials travel in the body, never the URL)

    @router.post("/recordings/{recording_id}/audio")
    async def recording_audio(recording_id: int, username: Optional[str] = Form(None),
                              password: Optional[str] = Form(None)):
        if recording_id <= 0:
            raise HTTPException(status_code=422, detail="Invalid recording ID.")
        creds = credentials(username, password)

        def open_stream():
            client = XcallyClient(settings, *creds)
            upstream = None
            try:
                upstream = client.recording_audio(recording_id)
                chunks = upstream.iter_content(chunk_size=64 * 1024)
                head = next(chunks, b"")
                info = identify(head, upstream.headers.get("Content-Type"),
                                filename_from_disposition(upstream.headers.get("Content-Disposition")))
                if info["media_type"] is None:
                    raise XcallyError(f"xCALLY returned {info['container']} instead of an audio file for recording {recording_id}.")
                return client, upstream, chunks, head, info
            except Exception:
                if upstream is not None:
                    upstream.close()
                client.close()
                raise

        client, upstream, chunks, head, info = await in_executor(open_stream)
        headers = {
            "Cache-Control": "no-store",
            "X-Recording-Format": info["container"],
            "X-Recording-Codec": info["codec"] or "unknown",
            "X-Recording-Playable": {True: "yes", False: "no", None: "unknown"}[info["playable"]],
        }

        if info.get("convert"):
            def convert():
                try:
                    data = bytearray(head)
                    for chunk in chunks:
                        data.extend(chunk)
                        if len(data) > MAX_GSM_BYTES:
                            raise XcallyError("This recording is too large to play in the browser.")
                    try:
                        return gsm_to_wav(bytes(data))
                    except (ValueError, RuntimeError) as exc:
                        raise XcallyError(f"Recording {recording_id} could not be converted for playback: {exc}") from exc
                finally:
                    upstream.close()
                    client.close()

            wav = await in_executor(convert)
            return Response(content=wav, media_type="audio/wav", headers=headers)

        def body():
            try:
                yield head
                yield from chunks
            finally:
                upstream.close()
                client.close()

        return StreamingResponse(body(), media_type=info["media_type"], headers=headers)

    # Evaluations (local only; xCALLY sync comes later)

    @router.post("/evaluations", status_code=201)
    async def create_evaluation(body: EvaluationIn) -> dict:
        try:
            return repository.create_evaluation(**body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/evaluations")
    async def list_evaluations(agent_id: Optional[int] = None, recording_id: Optional[int] = None) -> dict:
        return {"evaluations": repository.list_evaluations(agent_id=agent_id, recording_id=recording_id)}

    return router
