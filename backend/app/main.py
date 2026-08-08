"""ClearanceRoom API — streams the clearance pipeline over SSE.

In production (Cloud Run) it also serves the built frontend from STATIC_DIR.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .pipeline import run_pipeline
from .titleguard import check_title
from .truestory import run_truestory

app = FastAPI(title="ClearanceRoom", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
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
    script: str


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "mock_gemini": config.MOCK_GEMINI,
        "mock_parallel": config.MOCK_PARALLEL,
        "model": config.GEMINI_MODEL,
    }


@app.get("/api/sample")
async def sample(mode: str = "clearance") -> dict:
    title, path = SAMPLES.get(mode, SAMPLES["clearance"])
    return {"title": title, "script": path.read_text()}


@app.post("/api/clearance/run")
async def run(req: RunRequest) -> StreamingResponse:
    async def stream():
        async for event in run_pipeline(req.script):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/truestory/run")
async def truestory(req: RunRequest) -> StreamingResponse:
    async def stream():
        async for event in run_truestory(req.script):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class TitleRequest(BaseModel):
    title: str


@app.post("/api/title/check")
async def title_check(req: TitleRequest) -> StreamingResponse:
    async def stream():
        async for event in check_title(req.title):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Serve the built frontend in production (mounted last so /api/* wins).
_static_dir = os.getenv("STATIC_DIR", "")
if _static_dir and Path(_static_dir).is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
