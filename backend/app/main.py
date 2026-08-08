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

app = FastAPI(title="ClearanceRoom", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def _sample_script_path() -> Path:
    here = Path(__file__).resolve()
    # repo layout: backend/app/main.py -> ../../scripts; Docker: /app/app/main.py -> /app/scripts
    for base in (here.parents[2] if len(here.parents) > 2 else here.parents[1], here.parents[1]):
        candidate = base / "scripts" / "midnight_static.txt"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("scripts/midnight_static.txt not found")


SAMPLE_SCRIPT = _sample_script_path()


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
async def sample() -> dict:
    return {"title": "MIDNIGHT STATIC", "script": SAMPLE_SCRIPT.read_text()}


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


# Serve the built frontend in production (mounted last so /api/* wins).
_static_dir = os.getenv("STATIC_DIR", "")
if _static_dir and Path(_static_dir).is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
