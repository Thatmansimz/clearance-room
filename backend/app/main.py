"""ClearanceRoom API — streams the clearance pipeline over SSE.

In production (Cloud Run) it also serves the built frontend from STATIC_DIR.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .pipeline import run_pipeline
from .titleguard import check_title
from .truestory import run_truestory

app = FastAPI(title="ClearanceRoom", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cloud Run's request timeout covers the TOTAL duration of a streaming response,
# so the deploy must set --timeout well above the longest run (see docs/DEPLOY.md);
# heartbeats do not extend it. These frames instead keep proxies and browsers from
# treating a quiet stretch as a dead connection. Clients split on a blank line and
# only parse frames starting with "data: ", so comment frames are ignored.
HEARTBEAT_SECONDS = 15.0

# A public, unauthenticated endpoint that fans out to two paid APIs needs a cap.
_run_slots = asyncio.Semaphore(config.MAX_CONCURRENT_RUNS)


def _sse(source) -> StreamingResponse:
    """Wrap an event async-generator as an SSE response, hardened once for every
    streaming route: a bounded concurrency slot (fail fast when the box is busy)
    and keepalive heartbeats so a long run can't be dropped mid-stream."""

    async def stream():
        try:
            await asyncio.wait_for(_run_slots.acquire(), timeout=0.1)
        except asyncio.TimeoutError:
            busy = {"type": "error",
                    "message": "Server busy — a run is already in progress. Try again in a moment."}
            yield f"data: {json.dumps(busy)}\n\n"
            return

        queue: asyncio.Queue = asyncio.Queue()

        async def pump() -> None:
            try:
                async for event in source:
                    await queue.put(event)
            finally:
                await queue.put(None)

        task = asyncio.create_task(pump())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            task.cancel()
            _run_slots.release()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


def _script_path(filename: str) -> Path:
    here = Path(__file__).resolve()
    # repo layout: backend/app/main.py -> ../../scripts; Docker: /app/app/main.py -> /app/scripts
    for base in (here.parents[2] if len(here.parents) > 2 else here.parents[1], here.parents[1]):
        candidate = base / "scripts" / filename
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"scripts/{filename} not found")


SAMPLES = {
    "clearance": ("MIDNIGHT STATIC", _script_path("midnight_static.txt")),
    "truestory": ("STATIC & LIGHTNING", _script_path("static_and_lightning.txt")),
}


class RunRequest(BaseModel):
    # Cap the payload: this endpoint is public and every entity fans out to a
    # paid search. A whole-season dump would rack up cost with no upper bound.
    script: str = Field(min_length=1, max_length=config.MAX_SCRIPT_CHARS)


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "mock_gemini": config.MOCK_GEMINI,
        "mock_parallel": config.MOCK_PARALLEL,
        "model": config.GEMINI_MODEL,
        # Stage 4 runs a different model from stages 1 and 3; report both so a
        # dead report model is detectable from here before a demo.
        "models": {
            "breakdown": config.GEMINI_MODEL,
            "assess": config.GEMINI_MODEL,
            "report": config.GEMINI_REPORT_MODEL,
        },
        "limits": {
            "max_script_chars": config.MAX_SCRIPT_CHARS,
            "max_concurrent_runs": config.MAX_CONCURRENT_RUNS,
        },
    }


@app.get("/api/sample")
async def sample(mode: str = "clearance") -> dict:
    title, path = SAMPLES.get(mode, SAMPLES["clearance"])
    return {"title": title, "script": path.read_text()}


@app.post("/api/clearance/run")
async def run(req: RunRequest) -> StreamingResponse:
    return _sse(run_pipeline(req.script))


@app.post("/api/truestory/run")
async def truestory(req: RunRequest) -> StreamingResponse:
    return _sse(run_truestory(req.script))


class AiCheckRequest(BaseModel):
    usages: list[str] = Field(max_length=16)


@app.post("/api/eo/ai-check")
async def ai_check(req: AiCheckRequest) -> StreamingResponse:
    from .eobinder import run_ai_check

    return _sse(run_ai_check(req.usages))


class TitleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)


@app.post("/api/title/check")
async def title_check(req: TitleRequest) -> StreamingResponse:
    return _sse(check_title(req.title))


# Serve the built frontend in production (mounted last so /api/* wins).
_static_dir = os.getenv("STATIC_DIR", "")
if _static_dir and Path(_static_dir).is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
