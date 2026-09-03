# ClearanceRoom — complete source

Every file we wrote, in one place. Generated from the repo, so it is the code
that is actually deployed and running.

**Live:** https://clearanceroom-957638696965.us-central1.run.app
**Repo:** https://github.com/Thatmansimz/clearance-room

## Stack

| Layer | What |
|---|---|
| Extraction & grading | Gemini `gemini-3.6-flash` on Vertex AI |
| Executive reports | Gemini `gemini-3.1-pro-preview` |
| Agent framework | Google ADK (`LlmAgent` + `InMemoryRunner`) |
| Research — breadth | Parallel **Search API** via the official `parallel-web` SDK |
| Research — depth | Parallel **Task API** (same SDK) — per-field citations, reasoning, confidence |
| API | FastAPI, streaming over SSE |
| UI | React + Vite + Tailwind v4 |
| Hosting | Cloud Run (multi-stage Docker) |

## How the pieces fit

```
Screenplay
    |
    v
[1] BREAKDOWN   pipeline.py   Gemini + Google ADK, structured output
    |           every clearable item, with scene and usage context
    v
[2] RESEARCH    parallel_client.py   Parallel Search API (SDK)
    |           one research objective per item, bounded fan-out
    v
[3] ASSESS      pipeline.py   Gemini, JSON schema, temperature 0
    |           CLEAR / CAUTION / BLOCKED + risk score + fix
    v
[4] REPORT      pipeline.py   Gemini + ADK, then eobinder.py maps
                findings onto the 12 E&O underwriter procedures

    on demand:  dossier.py    Parallel Task API deep research
                per-field citations, reasoning, confidence
    modes:      truestory.py  defamation fact-check
                titleguard.py title collision sweep
```


## Backend — the agent

FastAPI + the four-stage pipeline. Everything streams over SSE as it happens.

### `backend/app/config.py`

Configuration and the per-service mock flags. `MOCK_MODE=1` forces full mock; otherwise each service goes live the moment its credentials exist.

```python
"""ClearanceRoom configuration.

Live mode requires:
  - GOOGLE_CLOUD_PROJECT + `gcloud auth application-default login` (Vertex AI)
  - PARALLEL_API_KEY (Parallel Search API)
Mock mode (MOCK_MODE=1) runs the full pipeline with canned data — no keys needed.
"""
import os

from dotenv import load_dotenv

load_dotenv()

MOCK_MODE = os.getenv("MOCK_MODE", "0") == "1"

# Google Cloud / Vertex AI
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_REPORT_MODEL = os.getenv("GEMINI_REPORT_MODEL", "gemini-3.1-pro-preview")

# Vertex AI routing for google-genai and ADK
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
if GOOGLE_CLOUD_PROJECT:
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", GOOGLE_CLOUD_PROJECT)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", GOOGLE_CLOUD_LOCATION)

# Parallel Search API
PARALLEL_API_KEY = os.getenv("PARALLEL_API_KEY", "")
PARALLEL_API_URL = os.getenv("PARALLEL_API_URL", "https://api.parallel.ai/v1/search")
PARALLEL_MODE = os.getenv("PARALLEL_MODE", "advanced")  # "advanced" | "turbo"
# Task API (Deep Dossier): deeper multi-hop research with per-field citations,
# reasoning, and confidence. "core" balances depth against demo-friendly latency.
# "-fast" variants are 2-5x quicker at identical pricing (measured: core-fast
# 28s vs core 150s on the same dossier), which is the difference between a
# dossier you can show on camera and one you have to cut away from.
PARALLEL_TASK_PROCESSOR = os.getenv("PARALLEL_TASK_PROCESSOR", "core-fast")
DOSSIER_POLL_SECONDS = float(os.getenv("DOSSIER_POLL_SECONDS", "5"))
DOSSIER_TIMEOUT_SECONDS = float(os.getenv("DOSSIER_TIMEOUT_SECONDS", "300"))

# Pipeline tuning
RESEARCH_CONCURRENCY = int(os.getenv("RESEARCH_CONCURRENCY", "4"))
# Every entity costs a paid search, so a single run's fan-out is bounded even if
# the breakdown stage returns hundreds of items from a feature-length script.
MAX_ENTITIES_PER_RUN = int(os.getenv("MAX_ENTITIES_PER_RUN", "60"))

# Abuse guards for public, unauthenticated, paid-API-backed endpoints.
MAX_SCRIPT_CHARS = int(os.getenv("MAX_SCRIPT_CHARS", "120000"))
MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "3"))

# Per-service mock flags: MOCK_MODE=1 forces full mock; otherwise each service
# goes live as soon as its credentials are present.
MOCK_GEMINI = MOCK_MODE or not GOOGLE_CLOUD_PROJECT
MOCK_PARALLEL = MOCK_MODE or not PARALLEL_API_KEY
```

### `backend/app/pipeline.py`

The core pipeline: ADK breakdown, deterministic research fan-out, evidence-gated assessment, executive report. Also the precedent cards and the deterministic fallback summary.

```python
"""ClearanceRoom pipeline — a deterministic, multi-step clearance agent.

Stages (fixed order, orchestrated in code — not left to model whim):
  1. BREAKDOWN   Google ADK LlmAgent (Gemini on Vertex AI) extracts every
                 clearable entity from the script as structured JSON.
  2. RESEARCH    Deterministic fan-out: each entity is researched via the
                 Parallel Search API for live web evidence.
  3. ASSESSMENT  Gemini (google-genai, structured output) scores each entity
                 against its evidence: CLEAR / CAUTION / BLOCKED.
  4. REPORT      Google ADK LlmAgent compiles the executive clearance report.

Events stream out as they happen so the UI can render the run live.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncGenerator
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from . import config, mockdata, parallel_client

VERDICTS = ("CLEAR", "CAUTION", "BLOCKED")
CATEGORIES = ("BRAND", "PERSON", "MUSIC", "ARTWORK", "LOCATION", "MEDIA", "ORGANIZATION", "OTHER")

# Documented real-world incidents, attached to non-CLEAR findings so severity
# reads in dollars, not adjectives. Sources: docs/RESEARCH.md (all visited).
PRECEDENTS: dict[str, dict[str, str]] = {
    "ARTWORK": {
        "case": "Fine-art images filmed without permission drew a $900K copyright claim",
        "url": "https://www.frontrowinsurance.com/errors-omissions-insurance-101",
    },
    "MUSIC": {
        "case": "A 'sound-alike' rendition drew a $65K misappropriation claim",
        "url": "https://www.frontrowinsurance.com/errors-omissions-insurance-101",
    },
    "PERSON": {
        "case": "Baby Reindeer's portrayal of a real person drew a $170M defamation suit",
        "url": "https://deadline.com/2024/09/baby-reindeer-netflix-trial-date-2025-1236085108/",
    },
    "MEDIA": {
        "case": "Studio-library clips license at roughly $5K-$25K per minute",
        "url": "https://www.filmindependent.org/blog/8-keys-to-successfully-delivering-your-film-this-festival-season/",
    },
    "ORGANIZATION": {
        "case": "E&O applications require a full cast clearance check on every character and "
                "business name — including invented ones that collide with a real business",
        "url": "https://kellyinsurancegroup.com/film-production-television-producers-errors-omissions-clearance-procedures/",
    },
    "BRAND": {
        "case": "Underwriters require distinctive products and logos to be cleared or "
                "greeked (swapped for a fictional brand) before a distributor will release",
        "url": "https://asset.trvstatic.com/download/assets/ee-eo-03.doc/164e06cc63ca11eeb95a06b700163137",
    },
    "LOCATION": {
        "case": "Written releases are required for distinctive locations and buildings; "
                "only non-distinctive background is exempt",
        "url": "https://asset.trvstatic.com/download/assets/ee-eo-03.doc/164e06cc63ca11eeb95a06b700163137",
    },
    "TITLE": {
        "case": "A YouTube series' common-law title rights won an injunction blocking T.I.'s finished film",
        "url": "https://www.billboard.com/pro/ti-movie-title-lawsuit-rapper-situationships-judge/",
    },
}


class Entity(BaseModel):
    name: str = Field(description="The exact clearable item, e.g. 'Nike windbreaker'")
    category: str = Field(description=f"One of {CATEGORIES}")
    scene: str = Field(description="Scene heading where it appears")
    context: str = Field(description="One sentence: how it is used in the script")


class Breakdown(BaseModel):
    entities: list[Entity]


class Assessment(BaseModel):
    verdict: str = Field(description="One of CLEAR, CAUTION, BLOCKED")
    risk_score: int = Field(description="0-100 legal risk score")
    rationale: str = Field(description="Two sentences max, cite the evidence")
    recommendation: str = Field(description="Concrete production guidance")


BREAKDOWN_INSTRUCTION = f"""You are a script clearance coordinator for a film studio.
Read the screenplay provided by the user and extract EVERY item that a clearance
report must cover: brand names and logos, real people (named or depicted), songs
or lyrics performed or quoted, artworks, film/TV clips shown on screen, named
real locations with trademark or access issues, business names, phone numbers,
and any other legally sensitive reference.

Rules:
- Include fictional business names too — they must be checked for conflicts.
- Categories: {", ".join(CATEGORIES)}.
- 'context' must capture HOW the item is used (worn, sung, shown, mentioned) —
  usage context changes legal risk.
- Be exhaustive. A missed item on a real production is a lawsuit.
Return only the structured breakdown."""

ASSESS_PROMPT = """You are entertainment clearance counsel. Assess ONE item from a
script breakdown using web research evidence gathered by the Parallel Search API.

ITEM: {name}  (category: {category})
USAGE IN SCRIPT: {context}
SCENE: {scene}

EVIDENCE:
{evidence}

Before grading, characterize the DEPICTION from the usage context above:
neutral, negative, or disparaging. A brand shown as defective, criminal,
counterfeit, or stolen is a tarnishment risk and can never be CLEAR on the
grounds that the appearance is "incidental".

Verdicts: CLEAR (customary/incidental use, no license needed), CAUTION (usable
with mitigation — greeking, wardrobe swap, counsel review), BLOCKED (do not shoot
as written without a license or rewrite). Usage context matters as much as the
item itself: disparaging or featured uses score higher risk than incidental ones.
Ground your rationale in the evidence provided, and gloss any industry term you
use (e.g. "greek the sign (swap it for a fictional brand)")."""

REPORT_INSTRUCTION = """You are the head of business & legal affairs at a film studio.
You will receive a JSON array of clearance assessments for one screenplay. Write a
tight executive summary (one paragraph, ~120 words) for the producer: overall
clearance posture, the blockers and their creative workarounds, the caution items
as a group, and estimated licensing exposure avoided if recommendations are
followed. Plain prose, no lists, studio voice."""


# ---------------------------------------------------------------- ADK helpers

def _adk_agent(name: str, instruction: str, output_schema: type[BaseModel] | None, model: str):
    from google.adk.agents import LlmAgent
    from google.genai import types as gt

    return LlmAgent(
        name=name,
        model=model,
        instruction=instruction,
        output_schema=output_schema,
        # Clearance is a legal workflow: the same script must grade the same way
        # twice. temperature=0 is what lets us call the whole pipeline reproducible.
        generate_content_config=gt.GenerateContentConfig(temperature=0.0),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


async def _run_adk(agent, message: str) -> str:
    """Run one ADK agent to completion, return its final response text."""
    from google.adk.runners import InMemoryRunner
    from google.genai import types as gt

    runner = InMemoryRunner(agent=agent, app_name="clearance-room")
    session = await runner.session_service.create_session(
        app_name="clearance-room", user_id="studio"
    )
    final = ""
    async for event in runner.run_async(
        user_id="studio",
        session_id=session.id,
        new_message=gt.Content(role="user", parts=[gt.Part(text=message)]),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final = "".join(p.text or "" for p in event.content.parts)
    return final


async def _run_adk_retrying(agent, message: str, attempts: int = 2) -> str:
    """Stage 1 and stage 4 are single points of failure for a whole run — a
    transient Vertex error there throws away every paid search. Retry once."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await _run_adk(agent, message)
        except Exception as exc:
            last = exc
            if attempt < attempts - 1:
                await asyncio.sleep(1.5)
    raise last  # type: ignore[misc]


def _fallback_summary(assessed: list[dict[str, Any]]) -> str:
    """Deterministic summary used when the report model is unavailable, so a
    completed run still produces a usable document."""
    counts = {v: sum(1 for a in assessed if a["verdict"] == v) for v in VERDICTS}
    blocked = [a["name"] for a in assessed if a["verdict"] == "BLOCKED"]
    lead = (
        f"{len(assessed)} items were researched and graded: {counts['BLOCKED']} blocked, "
        f"{counts['CAUTION']} caution, {counts['CLEAR']} clear."
    )
    if blocked:
        lead += " Blocked items requiring a license or rewrite before photography: " \
                + ", ".join(blocked[:8]) + "."
    return lead + " Per-item rationales and sources are listed below. (Executive " \
                  "summary generation was unavailable for this run.)"


# ------------------------------------------------------------- stage: assess

async def _assess_entity(entity: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    if config.MOCK_GEMINI:
        return await mockdata.mock_assess(entity, evidence)

    from google import genai
    from google.genai import types as gt

    client = genai.Client()
    evidence_text = json.dumps(evidence, indent=1) if evidence else "(no results found)"
    resp = await client.aio.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=ASSESS_PROMPT.format(
            name=entity["name"], category=entity["category"],
            context=entity["context"], scene=entity["scene"],
            evidence=evidence_text,
        ),
        config=gt.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Assessment,
            temperature=0.0,
        ),
    )
    result = Assessment.model_validate_json(resp.text).model_dump()
    result["verdict"] = result["verdict"].upper()
    if result["verdict"] not in VERDICTS:
        result["verdict"] = "CAUTION"
    # A green light must be earned by evidence. With no sources the verdict is
    # model prior only, so cap it — the tool over-flags rather than under-flags.
    if not evidence and result["verdict"] == "CLEAR":
        result["verdict"] = "CAUTION"
        result["rationale"] = (
            "No web evidence was retrieved for this item, so it cannot be cleared "
            "on the record. " + result["rationale"]
        )
    return result


# ---------------------------------------------------------------- the runner

async def run_pipeline(script_text: str) -> AsyncGenerator[dict[str, Any], None]:
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def emit(event: dict[str, Any]) -> None:
        await queue.put(event)

    async def work() -> None:
        t0 = time.monotonic()
        searches_fired = 0
        domains_seen: set[str] = set()
        try:
            # -- Stage 1: BREAKDOWN --------------------------------------
            await emit({"type": "stage", "stage": "breakdown", "status": "start"})
            if config.MOCK_GEMINI:
                entities = await mockdata.mock_breakdown(script_text)
            else:
                agent = _adk_agent("script_breakdown", BREAKDOWN_INSTRUCTION,
                                   Breakdown, config.GEMINI_MODEL)
                raw = await _run_adk_retrying(agent, script_text)
                parsed = Breakdown.model_validate_json(raw)
                entities = [
                    {"id": f"e{i+1}", **e.model_dump()}
                    for i, e in enumerate(parsed.entities)
                ]
            if len(entities) > config.MAX_ENTITIES_PER_RUN:
                await emit({"type": "warning", "id": "",
                            "message": f"Script produced {len(entities)} items; researching "
                                       f"the first {config.MAX_ENTITIES_PER_RUN}."})
                entities = entities[:config.MAX_ENTITIES_PER_RUN]
            for e in entities:
                await emit({"type": "entity_found", "entity": e})
            await emit({"type": "stage", "stage": "breakdown", "status": "done",
                        "count": len(entities)})

            # -- Stages 2+3: RESEARCH (Parallel) then ASSESS (Gemini) ----
            # Deterministic per-entity chain, fanned out with bounded
            # concurrency; results stream in as each entity completes.
            await emit({"type": "stage", "stage": "research", "status": "start"})
            assess_started = False
            sem = asyncio.Semaphore(config.RESEARCH_CONCURRENCY)
            assessed: list[dict[str, Any]] = []

            async def investigate(entity: dict[str, Any]) -> None:
                nonlocal assess_started, searches_fired
                async with sem:
                    await emit({"type": "entity_status", "id": entity["id"],
                                "status": "researching"})
                    try:
                        evidence = await parallel_client.research_entity(entity)
                    except Exception as exc:  # research failure downgrades, not aborts
                        evidence = []
                        await emit({"type": "warning", "id": entity["id"],
                                    "message": f"research failed: {exc}"})
                    searches_fired += 1
                    for ev_item in evidence:
                        host = urlparse(ev_item.get("url", "")).hostname
                        if host:
                            domains_seen.add(host.removeprefix("www."))
                    await emit({"type": "ticker", "searches": searches_fired,
                                "sources": len(domains_seen)})
                    if not assess_started:
                        assess_started = True
                        await emit({"type": "stage", "stage": "assess",
                                    "status": "start"})
                    if not evidence:
                        await emit({"type": "warning", "id": entity["id"],
                                    "message": f"no web evidence retrieved for {entity['name']} — verdict is model prior only"})
                    await emit({"type": "entity_status", "id": entity["id"],
                                "status": "assessing"})
                    try:
                        result = await _assess_entity(entity, evidence)
                    except Exception as exc:  # assessment failure degrades ONE item, not the run
                        await emit({"type": "warning", "id": entity["id"],
                                    "message": f"assessment failed: {exc}"})
                        result = {
                            "verdict": "CAUTION",
                            "risk_score": 50,
                            "rationale": (f"Automated assessment did not complete "
                                          f"({type(exc).__name__}). This item is UNREVIEWED — "
                                          f"treat as not cleared and route to counsel."),
                            "recommendation": "Re-run this item, or send it to counsel before locking the draft.",
                        }
                    record = {**entity, **result, "sources": [
                        {"url": ev["url"], "title": ev["title"]} for ev in evidence
                    ]}
                    if result["verdict"] != "CLEAR":
                        precedent = PRECEDENTS.get(entity["category"])
                        if precedent:
                            record["precedent"] = precedent
                    assessed.append(record)
                    await emit({"type": "entity_result", "id": entity["id"],
                                **result, "sources": record["sources"],
                                "precedent": record.get("precedent")})

            outcomes = await asyncio.gather(
                *(investigate(e) for e in entities), return_exceptions=True
            )
            # Second layer of defence: if investigate() itself crashed (e.g. an
            # emit path), the entity must still surface rather than vanish and
            # the remaining entities must not be taken down with it.
            for entity, outcome in zip(entities, outcomes):
                if isinstance(outcome, BaseException) and not any(a["id"] == entity["id"] for a in assessed):
                    result = {
                        "verdict": "CAUTION", "risk_score": 50,
                        "rationale": f"Processing error ({type(outcome).__name__}); item is UNREVIEWED.",
                        "recommendation": "Re-run this item or route it to counsel.",
                    }
                    assessed.append({**entity, **result, "sources": []})
                    await emit({"type": "entity_result", "id": entity["id"], **result,
                                "sources": [], "precedent": None})
            await emit({"type": "stage", "stage": "research", "status": "done"})
            await emit({"type": "stage", "stage": "assess", "status": "done"})

            # -- Stage 4: REPORT -----------------------------------------
            await emit({"type": "stage", "stage": "report", "status": "start"})
            order = {"BLOCKED": 0, "CAUTION": 1, "CLEAR": 2}
            assessed.sort(key=lambda a: (order.get(a["verdict"], 3), -a["risk_score"]))
            if config.MOCK_GEMINI:
                summary = await mockdata.mock_report(assessed)
            else:
                agent = _adk_agent("clearance_report", REPORT_INSTRUCTION,
                                   None, config.GEMINI_REPORT_MODEL)
                try:
                    summary = await _run_adk_retrying(agent, json.dumps(assessed, indent=1))
                except Exception:
                    # Never throw away a completed run over the summary stage.
                    summary = _fallback_summary(assessed)
            from .eobinder import build_checklist
            stats = {v: sum(1 for a in assessed if a["verdict"] == v) for v in VERDICTS}
            await emit({"type": "report", "summary": summary.strip(),
                        "stats": stats, "items": assessed,
                        "eo_checklist": build_checklist(assessed),
                        "elapsed_seconds": round(time.monotonic() - t0, 1),
                        "searches": searches_fired, "sources": len(domains_seen)})
            await emit({"type": "stage", "stage": "report", "status": "done"})
            await emit({"type": "done", "mock": config.MOCK_GEMINI or config.MOCK_PARALLEL})
        except Exception as exc:
            await emit({"type": "error", "message": str(exc)})
        finally:
            await queue.put(None)

    task = asyncio.create_task(work())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
    finally:
        task.cancel()
```

### `backend/app/parallel_client.py`

Parallel **Search API** via the official `parallel-web` SDK — breadth. Category-aware query construction, because a bare entity name returns the wrong industry.

```python
"""Parallel Search API client — the research engine of ClearanceRoom.

Runtime partner integration for the Parallel track: every entity extracted from
a script is researched through Parallel's Search API (POST /v1/search) to gather
live web evidence on trademark status, rights holders, and prior disputes.
"""
from __future__ import annotations

import asyncio
from typing import Any

from parallel import AsyncParallel

from . import config, mockdata

_client: AsyncParallel | None = None


def client() -> AsyncParallel:
    """The official Parallel SDK client, created once and reused."""
    global _client
    if _client is None:
        _client = AsyncParallel(api_key=config.PARALLEL_API_KEY)
    return _client


class ParallelSearchError(RuntimeError):
    pass


async def search(objective: str, search_queries: list[str]) -> dict[str, Any]:
    """Run one Parallel Search call. Returns the raw API response dict."""
    if config.MOCK_PARALLEL:
        return await mockdata.mock_search(objective)

    if not config.PARALLEL_API_KEY:
        raise ParallelSearchError("PARALLEL_API_KEY is not set (or enable MOCK_MODE=1)")

    try:
        resp = await client().search(
            objective=objective,
            search_queries=search_queries,
            mode=config.PARALLEL_MODE,
        )
    except Exception as exc:
        raise ParallelSearchError(f"Parallel Search failed: {exc}") from exc
    return resp.model_dump()


def evidence_from_response(response: dict[str, Any], max_results: int = 5) -> list[dict[str, Any]]:
    """Flatten a Parallel Search response into evidence items for the assessor."""
    evidence = []
    for r in response.get("results", [])[:max_results]:
        evidence.append(
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "publish_date": r.get("publish_date"),
                "excerpts": r.get("excerpts", [])[:3],
            }
        )
    return evidence


async def research_entity(entity: dict[str, Any]) -> list[dict[str, Any]]:
    """Build clearance-specific research queries for one entity and run them."""
    name = entity["name"]
    category = entity["category"]

    objectives = {
        "BRAND": (
            f"Determine the trademark status and owner of '{name}', and whether the "
            f"brand has a history of objecting to unlicensed depictions in film or TV."
        ),
        "PERSON": (
            f"Determine whether '{name}' is a living public figure, their publicity/"
            f"likeness rights posture, and any history of litigation over portrayals."
        ),
        "MUSIC": (
            f"Identify the rights holders (publishing and master) of the song '{name}', "
            f"and typical sync licensing considerations for film use."
        ),
        "ARTWORK": (
            f"Determine the copyright status and rights holder of the artwork '{name}', "
            f"and whether it can appear in a film without a license."
        ),
        "LOCATION": (
            f"Determine whether the location '{name}' has trademark or special filming "
            f"restrictions when depicted in film or TV."
        ),
        "MEDIA": (
            f"Identify who controls film/TV clip licensing for '{name}' and typical "
            f"requirements to show it on screen within another production."
        ),
        "ORGANIZATION": (
            f"Determine whether '{name}' matches any real company or registered "
            f"trademark that could create a conflict if used in a film."
        ),
    }
    objective = objectives.get(
        category,
        f"Research legal clearance considerations for depicting '{name}' in a film.",
    )

    # Bare entity names collide badly ("Casablanca" returns ceiling-fan
    # trademarks), so every query carries the category context stage 1 derived.
    CATEGORY_HINT = {
        "BRAND": "brand trademark owner",
        "PERSON": "person biography",
        "MUSIC": "song music rights",
        "ARTWORK": "artwork copyright artist",
        "LOCATION": "building location filming",
        "MEDIA": "film TV production rights holder",
        "ORGANIZATION": "business company name",
    }
    hint = CATEGORY_HINT.get(category, "")

    queries = [
        f"{name} {hint} trademark status",
        f"{name} {hint} film TV clearance licensing",
        f"{name} {hint} lawsuit depiction film",
    ]
    if category == "MEDIA":
        queries = [
            f"{name} film clip licensing rights holder studio",
            f"{name} film copyright owner distributor",
            f"{name} using clip in another film license cost",
        ]
    elif category == "ARTWORK":
        queries = [
            f"{name} artwork copyright rights holder",
            f"{name} artwork use in film license permission",
            f"{name} artist estate licensing",
        ]
    elif category == "MUSIC":
        queries = [
            f"{name} song publishing rights holder",
            f"{name} sync license film cost",
            f"{name} master recording owner",
        ]
    elif category == "PERSON":
        queries = [
            f"{name} right of publicity",
            f"{name} portrayal film lawsuit",
            f"is {name} alive public figure",
        ]

    response = await search(objective, queries)
    return evidence_from_response(response)


async def research_all(
    entities: list[dict[str, Any]],
    concurrency: int = 4,
) -> dict[str, list[dict[str, Any]]]:
    """Deterministic fan-out: research every entity with bounded concurrency."""
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, list[dict[str, Any]]] = {}

    async def one(entity: dict[str, Any]) -> None:
        async with sem:
            results[entity["id"]] = await research_entity(entity)

    await asyncio.gather(*(one(e) for e in entities))
    return results
```

### `backend/app/dossier.py`

Parallel **Task API** — depth on demand. Structured output where every field carries its own citations, reasoning, and confidence.

```python
"""Deep Dossier — Parallel Task API deep research on a single flagged item.

The Search API (stage 2) is breadth: one objective per entity, fast, so a whole
script grades in about a minute. It answers "is there a problem here?"

This is depth. When an item comes back BLOCKED or CAUTION, the producer's next
question is "so what do I actually do about it?" — who owns it, what does a
license cost, who do I email. That's a multi-hop research job, and it's what
Parallel's Task API is built for: a structured output schema, and for EVERY
field a `basis` carrying the citations, the reasoning, and a confidence rating.

Confidence is the part that matters for a legal workflow. "Sony Music Publishing
owns it, confidence: medium, here are the two pages that say so" is a usable
answer for counsel. A bare assertion is not.

Endpoints (verified live against the account):
  POST /v1/tasks/runs            -> {run_id, status}
  GET  /v1/tasks/runs/{id}       -> {status}
  GET  /v1/tasks/runs/{id}/result-> {output: {content, basis: [{field, citations, reasoning, confidence}]}}
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator

from . import config, mockdata
from .parallel_client import client

# What a producer needs in order to act on a flagged item. Every one of these
# fields comes back with its own citations + confidence from the Task API.
DOSSIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rights_holders": {
            "type": "string",
            "description": "Who owns or controls the rights, named precisely. "
                           "Include every layer (e.g. publishing AND master for music).",
        },
        "licensing_path": {
            "type": "string",
            "description": "Exactly how a production licenses this: department, "
                           "form, or contact channel. Name it if published.",
        },
        "typical_cost": {
            "type": "string",
            "description": "Reported or typical license fee range for film use, "
                           "with the basis for the figure. Say so if not public.",
        },
        "enforcement_history": {
            "type": "string",
            "description": "Documented disputes, lawsuits, or refusals involving "
                           "this item being used on screen.",
        },
        "cheapest_cure": {
            "type": "string",
            "description": "The lowest-cost way to keep the scene: substitution, "
                           "public-domain alternative, or negotiated license.",
        },
    },
    "required": ["rights_holders", "licensing_path", "typical_cost",
                 "enforcement_history", "cheapest_cure"],
    "additionalProperties": False,
}

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "item": {"type": "string"},
        "category": {"type": "string"},
        "usage_in_script": {"type": "string"},
    },
    "required": ["item", "category", "usage_in_script"],
    "additionalProperties": False,
}


class DossierError(RuntimeError):
    pass


async def _submit(entity: dict[str, Any]) -> str:
    run = await client().task_run.create(
        processor=config.PARALLEL_TASK_PROCESSOR,
        input={
            "item": entity["name"],
            "category": entity["category"],
            "usage_in_script": entity["context"],
        },
        task_spec={
            "input_schema": {"type": "json", "json_schema": INPUT_SCHEMA},
            "output_schema": {"type": "json", "json_schema": DOSSIER_SCHEMA},
        },
    )
    return run.run_id


async def run_dossier(entity: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
    """Stream a deep-research dossier for one flagged item."""
    if config.MOCK_PARALLEL:
        async for ev in mockdata.mock_dossier(entity):
            yield ev
        return

    try:
        yield {"type": "dossier_stage", "status": "submitted",
               "processor": config.PARALLEL_TASK_PROCESSOR, "item": entity["name"]}

        run_id = await _submit(entity)
        yield {"type": "dossier_stage", "status": "running", "run_id": run_id}

        # Poll for progress so the UI can show the run is alive, then fetch
        # the result. The SDK's result() long-polls, so the last call blocks
        # until the run finishes rather than spinning.
        deadline = config.DOSSIER_TIMEOUT_SECONDS
        waited = 0.0
        status = "queued"
        while waited < deadline:
            await asyncio.sleep(config.DOSSIER_POLL_SECONDS)
            waited += config.DOSSIER_POLL_SECONDS
            run = await client().task_run.retrieve(run_id)
            status = run.status
            yield {"type": "dossier_tick", "status": status, "elapsed": round(waited)}
            if status in ("completed", "failed", "cancelled"):
                break

        if status != "completed":
            raise DossierError(f"deep research did not complete (status: {status})")

        result = await client().task_run.result(run_id, api_timeout=120)
        output = result.output.model_dump()

        content = output.get("content", {})
        basis_by_field = {b["field"]: b for b in output.get("basis", [])}
        fields = []
        for key in DOSSIER_SCHEMA["properties"]:
            b = basis_by_field.get(key, {})
            fields.append({
                "field": key,
                "label": key.replace("_", " "),
                "value": content.get(key, ""),
                "confidence": b.get("confidence", "unknown"),
                "reasoning": b.get("reasoning", ""),
                "citations": [
                    {"title": c.get("title", ""), "url": c.get("url", "")}
                    for c in b.get("citations", [])[:4]
                ],
            })

        sources = {c["url"] for f in fields for c in f["citations"] if c["url"]}
        yield {"type": "dossier_result", "item": entity["name"], "fields": fields,
               "source_count": len(sources)}
    except Exception as exc:
        yield {"type": "error", "message": str(exc)}
```

### `backend/app/truestory.py`

True-Story Shield: extracts every factual claim about a real person and verifies it against the public record.

```python
"""True-Story Shield — docudrama defamation fact-check.

Fact-based films assert claims about real, identifiable people. Baby Reindeer
depicted a woman as a twice-convicted stalker with no conviction on the public
record; a federal judge let a $170M defamation suit proceed on exactly that
gap. True-Story Shield runs the check the court said was missing:

  1. CLAIMS      Google ADK LlmAgent (Gemini) extracts every factual assertion
                 the script makes about an identifiable real person.
  2. RESEARCH    Each assertion is verified against the live public record via
                 the Parallel Search API (news archives, court coverage,
                 historical record).
  3. ASSESSMENT  Gemini grades each claim: CLEAR (supported / non-defamatory),
                 CAUTION (unverifiable — fictionalize), BLOCKED (contradicted
                 by the public record — the Baby Reindeer gap).
  4. REPORT      ADK LlmAgent writes the defamation-exposure summary.

Emits the same event protocol as the clearance pipeline, so the same UI board
renders both.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncGenerator
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from . import config, parallel_client
from .pipeline import (
    PRECEDENTS,
    VERDICTS,
    _adk_agent,
    _fallback_summary,
    _run_adk_retrying,
)

CLAIM_CATEGORIES = (
    "CRIMINAL", "PROFESSIONAL", "FINANCIAL", "RELATIONSHIP",
    "HEALTH", "QUOTE", "OTHER",
)


class Claim(BaseModel):
    person: str = Field(description="The identifiable real person the claim is about")
    assertion: str = Field(description="The factual claim as the script asserts it, one sentence")
    presentation: str = Field(description="How the script asserts it: depicted | narrated | dialogue | implied")
    scene: str = Field(description="Scene heading or title card where it appears")
    category: str = Field(description=f"One of {CLAIM_CATEGORIES}")


class ClaimSheet(BaseModel):
    claims: list[Claim]


class ClaimVerdict(BaseModel):
    verdict: str = Field(description="One of CLEAR, CAUTION, BLOCKED")
    risk_score: int = Field(description="0-100 defamation/inaccuracy exposure")
    rationale: str = Field(description="Three sentences max: what the public record shows, citing the evidence")
    recommendation: str = Field(description="Concrete script guidance: keep, soften, fictionalize, composite, or cut")


CLAIMS_INSTRUCTION = f"""You are defamation-review counsel for a fact-based film
("based on a true story"). Read the screenplay and extract EVERY factual
assertion it makes about an identifiable real person — through narration,
dialogue, depiction, or implication. Include claims about: criminal conduct,
professional achievements or failures, finances, relationships and marital
status, health, direct quotes attributed to the person, and death/biographical
facts.

Rules:
- One claim per distinct assertion; split compound statements.
- 'assertion' states exactly what the script claims as fact.
- 'presentation' matters legally: depicted and narrated claims carry more
  weight than a character's opinion in dialogue.
- Categories: {", ".join(CLAIM_CATEGORIES)}.
- Be exhaustive. An unverified claim about a real person is how a $170M
  defamation suit starts.
Return only the structured claim sheet."""

ASSESS_CLAIM_PROMPT = """You are defamation counsel reviewing ONE factual claim a
"based on a true story" script makes about a real person, against live public-
record research gathered by the Parallel Search API.

PERSON: {person}
SCRIPT ASSERTS: {assertion}
PRESENTED AS: {presentation}
SCENE: {scene}

PUBLIC-RECORD EVIDENCE:
{evidence}

Grade the claim:
- CLEAR: the public record supports it, or it is non-defamatory and immaterial.
- CAUTION: the record neither supports nor contradicts it (unverifiable) — the
  script should fictionalize, composite, or soften; note that 'no record found'
  for a damaging claim is exactly the gap that sustained the Baby Reindeer
  $170M suit.
- BLOCKED: the public record CONTRADICTS the claim, or it asserts criminal or
  disgraceful conduct with no record support — do not shoot as written.
Risk score = defamation/inaccuracy exposure 0-100 (damaging + unsupported
scores highest; supported biographical facts score lowest). Ground the
rationale in the evidence."""

TS_REPORT_INSTRUCTION = """You are lead defamation counsel at a studio. You will
receive a JSON array of fact-check verdicts for a "based on a true story"
screenplay. Write a tight executive summary (~120 words, one paragraph) for
the producer: overall accuracy posture, which claims are contradicted by the
public record (name them), which are unverifiable and need fictionalization,
and the litigation exposure if shot as written — invoke the Baby Reindeer
$170M suit as the operative precedent for unverified damaging claims. Plain
prose, studio voice."""


async def _research_claim(claim: dict[str, Any]) -> list[dict[str, Any]]:
    person, assertion = claim["person"], claim["assertion"]
    objective = (
        f"Verify against the public record whether this claim about {person} "
        f"is true, false, or unsupported: \"{assertion}\". Prefer primary "
        f"sources: news archives, court records coverage, encyclopedic and "
        f"historical sources."
    )
    queries = [
        f"{person} {assertion[:70]}",
        f"{person} biography facts",
    ]
    if claim["category"] == "CRIMINAL":
        queries.append(f"{person} conviction court record")
    elif claim["category"] == "QUOTE":
        queries.append(f'"{assertion[:50]}" quote attribution')
    else:
        queries.append(f"{person} {claim['category'].lower()} history")

    response = await parallel_client.search(objective, queries)
    return parallel_client.evidence_from_response(response)


async def _assess_claim(claim: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    from google import genai
    from google.genai import types as gt

    client = genai.Client()
    resp = await client.aio.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=ASSESS_CLAIM_PROMPT.format(
            person=claim["person"], assertion=claim["assertion"],
            presentation=claim["presentation"], scene=claim["scene"],
            evidence=json.dumps(evidence, indent=1) if evidence else "(no results found)",
        ),
        config=gt.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ClaimVerdict,
            temperature=0.0,
        ),
    )
    result = ClaimVerdict.model_validate_json(resp.text).model_dump()
    result["verdict"] = result["verdict"].upper()
    if result["verdict"] not in VERDICTS:
        result["verdict"] = "CAUTION"
    return result


async def run_truestory(script_text: str) -> AsyncGenerator[dict[str, Any], None]:
    if config.MOCK_GEMINI or config.MOCK_PARALLEL:
        yield {"type": "error",
               "message": "True-Story Shield requires live mode (Vertex + Parallel keys)."}
        return

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def emit(event: dict[str, Any]) -> None:
        await queue.put(event)

    async def work() -> None:
        t0 = time.monotonic()
        searches_fired = 0
        domains_seen: set[str] = set()
        try:
            # -- Stage 1: CLAIMS -----------------------------------------
            await emit({"type": "stage", "stage": "breakdown", "status": "start"})
            agent = _adk_agent("claim_extraction", CLAIMS_INSTRUCTION,
                               ClaimSheet, config.GEMINI_MODEL)
            raw = await _run_adk_retrying(agent, script_text)
            parsed = ClaimSheet.model_validate_json(raw)
            claims = []
            for i, c in enumerate(parsed.claims):
                d = c.model_dump()
                claims.append({
                    "id": f"c{i+1}",
                    "name": d["person"],
                    "category": d["category"],
                    "scene": d["scene"],
                    "context": f'Script asserts ({d["presentation"]}): {d["assertion"]}',
                    **d,
                })
            for c in claims:
                await emit({"type": "entity_found", "entity": {
                    k: c[k] for k in ("id", "name", "category", "scene", "context")
                }})
            await emit({"type": "stage", "stage": "breakdown", "status": "done",
                        "count": len(claims)})

            # -- Stages 2+3: RESEARCH + ASSESS ---------------------------
            await emit({"type": "stage", "stage": "research", "status": "start"})
            assess_started = False
            sem = asyncio.Semaphore(config.RESEARCH_CONCURRENCY)
            assessed: list[dict[str, Any]] = []

            async def investigate(claim: dict[str, Any]) -> None:
                nonlocal assess_started, searches_fired
                async with sem:
                    await emit({"type": "entity_status", "id": claim["id"],
                                "status": "researching"})
                    try:
                        evidence = await _research_claim(claim)
                    except Exception as exc:
                        evidence = []
                        await emit({"type": "warning", "id": claim["id"],
                                    "message": f"research failed: {exc}"})
                    searches_fired += 1
                    for ev_item in evidence:
                        host = urlparse(ev_item.get("url", "")).hostname
                        if host:
                            domains_seen.add(host.removeprefix("www."))
                    await emit({"type": "ticker", "searches": searches_fired,
                                "sources": len(domains_seen)})
                    if not assess_started:
                        assess_started = True
                        await emit({"type": "stage", "stage": "assess",
                                    "status": "start"})
                    await emit({"type": "entity_status", "id": claim["id"],
                                "status": "assessing"})
                    try:
                        result = await _assess_claim(claim, evidence)
                    except Exception as exc:
                        # One flaky assessment must not abort a run that has
                        # already paid for every other claim's research.
                        await emit({"type": "warning", "id": claim["id"],
                                    "message": f"assessment failed: {exc}"})
                        result = {
                            "verdict": "CAUTION", "risk_score": 50,
                            "rationale": "Automated assessment failed for this claim; "
                                         "it was not reviewed and needs manual counsel review.",
                            "recommendation": "Re-run this claim or review it manually.",
                        }
                    record = {**claim, **result, "sources": [
                        {"url": ev["url"], "title": ev["title"]} for ev in evidence
                    ]}
                    if result["verdict"] != "CLEAR":
                        record["precedent"] = PRECEDENTS["PERSON"]
                    assessed.append(record)
                    await emit({"type": "entity_result", "id": claim["id"],
                                **result, "sources": record["sources"],
                                "precedent": record.get("precedent")})

            await asyncio.gather(*(investigate(c) for c in claims),
                                 return_exceptions=True)
            # Any claim whose whole chain died still gets a row — a silently
            # missing claim is worse than a flagged unreviewed one.
            done_ids = {a["id"] for a in assessed}
            for claim in claims:
                if claim["id"] not in done_ids:
                    fallback = {
                        "verdict": "CAUTION", "risk_score": 50,
                        "rationale": "This claim could not be processed and was not reviewed.",
                        "recommendation": "Review manually before shooting.",
                    }
                    assessed.append({**claim, **fallback, "sources": []})
                    await emit({"type": "entity_result", "id": claim["id"],
                                **fallback, "sources": [], "precedent": None})
            await emit({"type": "stage", "stage": "research", "status": "done"})
            await emit({"type": "stage", "stage": "assess", "status": "done"})

            # -- Stage 4: REPORT -----------------------------------------
            await emit({"type": "stage", "stage": "report", "status": "start"})
            order = {"BLOCKED": 0, "CAUTION": 1, "CLEAR": 2}
            assessed.sort(key=lambda a: (order.get(a["verdict"], 3), -a["risk_score"]))
            report_agent = _adk_agent("defamation_report", TS_REPORT_INSTRUCTION,
                                      None, config.GEMINI_REPORT_MODEL)
            try:
                summary = await _run_adk_retrying(report_agent, json.dumps(assessed, indent=1))
            except Exception:
                summary = _fallback_summary(assessed)
            from .eobinder import build_checklist
            stats = {v: sum(1 for a in assessed if a["verdict"] == v) for v in VERDICTS}
            # Claim categories don't map to clearance rows; the binder still
            # renders with persons_depicted fed by TRUESTORY findings.
            binder_items = [{**a, "category": "TRUESTORY"} for a in assessed]
            await emit({"type": "report", "summary": summary.strip(),
                        "stats": stats, "items": assessed,
                        "eo_checklist": build_checklist(binder_items),
                        "elapsed_seconds": round(time.monotonic() - t0, 1),
                        "searches": searches_fired, "sources": len(domains_seen)})
            await emit({"type": "stage", "stage": "report", "status": "done"})
            await emit({"type": "done", "mock": False})
        except Exception as exc:
            await emit({"type": "error", "message": str(exc)})
        finally:
            await queue.put(None)

    task = asyncio.create_task(work())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
    finally:
        task.cancel()
```

### `backend/app/titleguard.py`

TitleGuard: a two-pronged title sweep — registered marks *and* the common-law web uses trademark databases miss.

```python
"""TitleGuard — working-title collision sweep.

Registered-mark searches miss the killers: common-law title uses living on the
open web (web series, podcasts, self-published books). A 2016 YouTube series
titled "Situationships" won a 2025 injunction blocking T.I.'s finished film
from release. TitleGuard runs both sweeps — formal registrations AND the open
web — via Parallel Search, scores likelihood of confusion with Gemini, and
proposes cleared alternates.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

from pydantic import BaseModel, Field

from . import config, mockdata, parallel_client

TG_VERDICTS = ("CLEAR", "CAUTION", "BLOCKED")


class TitleConflict(BaseModel):
    name: str = Field(description="The conflicting work or mark, as titled")
    medium: str = Field(description="What it is: film, web series, podcast, trademark, book, etc.")
    year: str = Field(description="Year or year range if known, else 'unknown'")
    url: str = Field(description="Source URL evidencing the conflict")
    severity: str = Field(description="HIGH (same industry/medium, active) | MEDIUM | LOW")


class TitleVerdict(BaseModel):
    verdict: str = Field(description="One of CLEAR, CAUTION, BLOCKED")
    risk_score: int = Field(description="0-100 likelihood-of-confusion risk")
    conflicts: list[TitleConflict]
    rationale: str = Field(description="Three sentences max, grounded in the evidence")
    recommendation: str = Field(description="Concrete guidance: use it, secure it, or retitle")


class AlternateVerdict(BaseModel):
    verdict: str = Field(description="One of CLEAR, CAUTION, BLOCKED")
    note: str = Field(description="One line: why this alternate is or isn't safe")


class Alternates(BaseModel):
    titles: list[str] = Field(description="Exactly 3 alternate titles")


ASSESS_TITLE_PROMPT = """You are entertainment clearance counsel doing a film TITLE
clearance. Titles get no copyright, but existing uses create trademark and
unfair-competition exposure — including COMMON-LAW uses never registered
anywhere (a YouTube web series titled 'Situationships' won a 2025 injunction
blocking a finished studio film's release).

PROPOSED TITLE: {title}
PROJECT: feature film / scripted content

WEB EVIDENCE (registered marks sweep + open-web common-law sweep, via Parallel
Search API):
{evidence}

Identify every plausible conflicting use in the evidence. Weigh: same medium >
adjacent media > unrelated goods; active/recent > dormant; distinctive title >
generic phrase. Score likelihood-of-confusion risk 0-100 and give a verdict:
CLEAR (no meaningful conflicts), CAUTION (conflicts exist, survivable with
counsel review or coexistence), BLOCKED (do not market under this title).
Ground every conflict in an evidence URL."""

ALTERNATES_PROMPT = """A film cannot use the working title "{title}" (or wants
safer options). Known conflicts:
{conflicts}

Propose exactly 3 alternate titles that: keep the tone and meaning of the
original, are distinctive enough to clear (avoid generic phrases), and avoid
the conflict patterns above. Titles only."""

ASSESS_ALTERNATE_PROMPT = """Quick title-clearance screen for the proposed film
title "{title}". Web evidence from Parallel Search:
{evidence}

Verdict CLEAR / CAUTION / BLOCKED plus a one-line note. CLEAR if no meaningful
same-or-adjacent-media use appears in evidence."""


async def _gemini_json(prompt: str, schema: type[BaseModel], model: str | None = None) -> Any:
    from google import genai
    from google.genai import types as gt

    client = genai.Client()
    resp = await client.aio.models.generate_content(
        model=model or config.GEMINI_MODEL,
        contents=prompt,
        config=gt.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.0,
        ),
    )
    return schema.model_validate_json(resp.text)


async def _sweep(title: str) -> tuple[list[dict[str, Any]], int]:
    """Two-pronged Parallel sweep: registrations + open-web common-law uses."""
    formal = parallel_client.search(
        objective=(
            f"Find registered trademarks, films, TV series, or other formally "
            f"registered or released works titled '{title}' that could conflict "
            f"with a new feature film using this title."
        ),
        search_queries=[
            f'"{title}" trademark',
            f'"{title}" film movie title',
            f'"{title}" TV series IMDb',
        ],
    )
    common_law = parallel_client.search(
        objective=(
            f"Find common-law uses of the title '{title}' that trademark "
            f"databases miss: web series, YouTube channels, podcasts, "
            f"self-published books, games, or streaming content using this title."
        ),
        search_queries=[
            f'"{title}" web series YouTube',
            f'"{title}" podcast',
            f'"{title}" book novel',
        ],
    )
    responses = await asyncio.gather(formal, common_law)
    evidence: list[dict[str, Any]] = []
    for resp in responses:
        evidence.extend(parallel_client.evidence_from_response(resp, max_results=6))
    return evidence, len(responses)


async def check_title(title: str) -> AsyncGenerator[dict[str, Any], None]:
    title = title.strip()
    if config.MOCK_GEMINI or config.MOCK_PARALLEL:
        async for ev in mockdata.mock_title_check(title):
            yield ev
        return

    try:
        # -- Sweep: two Parallel searches (registered + common-law) ----------
        yield {"type": "tg_stage", "stage": "sweep", "status": "start"}
        evidence, searches = await _sweep(title)
        yield {
            "type": "tg_stage", "stage": "sweep", "status": "done",
            "searches": searches, "sources": len(evidence),
        }

        # -- Assess: Gemini likelihood-of-confusion scoring ------------------
        yield {"type": "tg_stage", "stage": "assess", "status": "start"}
        verdict = await _gemini_json(
            ASSESS_TITLE_PROMPT.format(
                title=title,
                evidence=json.dumps(evidence, indent=1) if evidence else "(none found)",
            ),
            TitleVerdict,
        )
        v = verdict.model_dump()
        v["verdict"] = v["verdict"].upper()
        if v["verdict"] not in TG_VERDICTS:
            v["verdict"] = "CAUTION"
        yield {"type": "tg_verdict", "title": title, **v}
        yield {"type": "tg_stage", "stage": "assess", "status": "done"}

        # -- Alternates: only when the title isn't clean ---------------------
        if v["verdict"] != "CLEAR":
            yield {"type": "tg_stage", "stage": "alternates", "status": "start"}
            alts = await _gemini_json(
                ALTERNATES_PROMPT.format(
                    title=title,
                    conflicts=json.dumps(v["conflicts"], indent=1) or "(none)",
                ),
                Alternates,
            )

            async def screen(alt: str) -> dict[str, Any]:
                resp = await parallel_client.search(
                    objective=(
                        f"Find any existing films, series, podcasts, books, or "
                        f"trademarks titled '{alt}' that could conflict with a "
                        f"new film using this title."
                    ),
                    search_queries=[f'"{alt}" film series title', f'"{alt}" trademark'],
                )
                alt_evidence = parallel_client.evidence_from_response(resp, max_results=4)
                av = await _gemini_json(
                    ASSESS_ALTERNATE_PROMPT.format(
                        title=alt,
                        evidence=json.dumps(alt_evidence, indent=1) if alt_evidence else "(none found)",
                    ),
                    AlternateVerdict,
                )
                out = av.model_dump()
                out["verdict"] = out["verdict"].upper()
                if out["verdict"] not in TG_VERDICTS:
                    out["verdict"] = "CAUTION"
                return {"type": "tg_alternate", "title": alt, **out}

            for coro in asyncio.as_completed([screen(a) for a in alts.titles[:3]]):
                yield await coro
            yield {"type": "tg_stage", "stage": "alternates", "status": "done"}

        yield {"type": "tg_done"}
    except Exception as exc:
        yield {"type": "error", "message": str(exc)}
```

### `backend/app/eobinder.py`

The E&O Binder: maps findings onto the twelve clearance procedures producer E&O applications actually require, plus the live AI-usage insurability intake.

```python
"""E&O Binder — the underwriter's view of a clearance run.

E&O (errors & omissions) insurance gates all distribution: no carrier, no
distributor. Producer E&O applications require a standard set of clearance
procedures. The Binder maps every ClearanceRoom finding onto that checklist,
so the run's output reads as the gating business document — not an AI report.

2025-2026 policies added generative-AI exclusions and disclosure requirements;
the AI-usage intake researches current carrier language live (Parallel) and
grades each disclosed usage (Gemini).

Checklist wording and citations are grounded in underwriter/broker sources —
see docs/EO_GROUNDING.md and the source_url on each row.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from pydantic import BaseModel, Field

from . import config, parallel_client
from .titleguard import _gemini_json


# The standard producer-E&O clearance procedures, grounded in primary sources
# (Travelers/UA E&O applications, Front Row broker guidance, 2026 AI-gap
# analysis) — see docs/EO_GROUNDING.md. maps_to [] = requires executed
# documents, outside a script scan.
CHECKLIST: list[dict[str, Any]] = [
    {"id": "script_clearance_review", "title": "Script Clearance Review (All Stages)",
     "requirement": "The script must be read prior to commencement of production to eliminate matter that is defamatory, invades privacy, or is otherwise actionable, and the shooting script and rough cuts must be re-checked through final cut since dialogue and persons may be added during photography.",
     "maps_to": ["BRAND", "PERSON", "MUSIC", "ARTWORK", "LOCATION", "MEDIA", "ORGANIZATION", "OTHER"],
     "source_url": "https://www.frontrowinsurance.com/news/film-eo-insurance-clearance-procedures-for-producers-part-1-of-3/"},
    {"id": "copyright_chain_of_title", "title": "Copyright Report and Chain of Title",
     "requirement": "Unless the work is an unpublished original not based on any other work, a copyright report covering domestic and foreign copyrights and renewal rights must be obtained on the property and any underlying copyrighted material.",
     "maps_to": [],
     "source_url": "https://asset.trvstatic.com/download/assets/ee-eo-03.doc/164e06cc63ca11eeb95a06b700163137"},
    {"id": "title_report", "title": "Title Report",
     "requirement": "Prior to final title selection, a title report must be obtained from a recognized title clearance service, and the title must be changed if the report shows conflicting prior uses.",
     "maps_to": ["TITLE"],
     "source_url": "https://www.frontrowinsurance.com/errors-omissions-insurance-101"},
    {"id": "original_work_origins", "title": "Origins of Original Work and Similar Submissions",
     "requirement": "If the script is an unpublished original, the origins of its basic idea, sequence of events, and characters must be ascertained, together with whether similar properties have been submitted to the applicant and why any submitting party could not claim theft or infringement.",
     "maps_to": [],
     "source_url": "https://asset.trvstatic.com/download/assets/ee-eo-03.doc/164e06cc63ca11eeb95a06b700163137"},
    {"id": "living_person_releases", "title": "Releases from Recognizable Living Persons",
     "requirement": "No names, faces, or likenesses of recognizable living persons \u2014 including thinly disguised versions identifiable from context \u2014 may be used without written releases granting the right to edit, juxtapose, and fictionalize, dispensed with only for fleeting background or with written justification accepted by the insurer.",
     "maps_to": ["PERSON"],
     "source_url": "https://asset.trvstatic.com/download/assets/ee-eo-03.doc/164e06cc63ca11eeb95a06b700163137"},
    {"id": "fictitious_name_checks", "title": "Fictitious Name Checks (Full Cast Clearance)",
     "requirement": "For fictional works, a full cast script clearance check must be performed on all character names, business names, and similar identifiers with all recommended changes made, and even deceased persons must be cleared for publicity rights where there is considerable fictionalization.",
     "maps_to": ["PERSON", "ORGANIZATION", "BRAND"],
     "source_url": "https://kellyinsurancegroup.com/film-production-television-producers-errors-omissions-clearance-procedures/"},
    {"id": "true_story_primary_sources", "title": "Primary Sources for Portrayals of Actual Events",
     "requirement": "If the production portrays actual events or persons, the author's sources must be shown to be independent and primary (contemporaneous news reports, court transcripts, witness interviews) rather than secondary copyrighted works such as another author's book or magazine articles.",
     "maps_to": ["TRUESTORY", "PERSON"],
     "source_url": "https://asset.trvstatic.com/download/assets/ee-eo-03.doc/164e06cc63ca11eeb95a06b700163137"},
    {"id": "music_licenses", "title": "Music Synchronization and Performance Licenses",
     "requirement": "Synchronization and performance licenses must be obtained from the composer or copyright owner of all music used, plus master-use licenses from recording owners for previously recorded music, unless both the composition and its arrangement are in the public domain.",
     "maps_to": ["MUSIC"],
     "source_url": "https://www.insuredproduction.com/wp-content/uploads/2016/07/UA-Producer%E2%80%99s-EO-Application-EO-Form.pdf"},
    {"id": "clip_stock_footage_licenses", "title": "Film Clip and Stock Footage Licenses",
     "requirement": "For every film clip or stock element, authorization must be obtained from the owner and from all contributors to the clip \u2014 underlying literary and musical rights holders, actors, and musicians \u2014 with new synchronization and performance licenses secured for any music embedded in the clip.",
     "maps_to": ["MEDIA"],
     "source_url": "https://www.insuredproduction.com/wp-content/uploads/2016/07/UA-Producer%E2%80%99s-EO-Application-EO-Form.pdf"},
    {"id": "location_property_releases", "title": "Location and Distinctive Property Releases",
     "requirement": "Written releases must be secured for distinctive locations, buildings, businesses, artwork, personal property, or products that are filmed, with releases unnecessary only where real property appears as non-distinctive background.",
     "maps_to": ["LOCATION", "ARTWORK", "BRAND"],
     "source_url": "https://asset.trvstatic.com/download/assets/ee-eo-03.doc/164e06cc63ca11eeb95a06b700163137"},
    {"id": "creator_agreements_all_media_rights", "title": "Creator Agreements and All-Media Distribution Rights",
     "requirement": "Written agreements must exist with all creators, writers, performers, and providers of material or on-screen services, and all contracts and releases must grant the applicant the right to market the production in all media and markets, with any gaps in underlying rights notified to the insurer.",
     "maps_to": [],
     "source_url": "https://asset.trvstatic.com/download/assets/ee-eo-03.doc/164e06cc63ca11eeb95a06b700163137"},
    {"id": "ai_usage_disclosure", "title": "Generative AI Usage Disclosure",
     "requirement": "All generative AI use \u2014 script material, imagery, music, voices, likenesses, or vendor AI-assisted work \u2014 must be disclosed on the application, specifying which tools were used at which production stages and what percentage of content involved AI, with documented consent for any digital replica; non-disclosure at the application stage is the fastest path to a denied claim.",
     "maps_to": ["AI"],
     "source_url": "https://www.akkerins.com/new-blog/ai-film-production-insurance-gap-2026"},
]


def build_checklist(assessed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministically map a clearance run's findings onto the E&O checklist."""
    order = {"BLOCKED": 0, "CAUTION": 1, "CLEAR": 2}
    rows = []
    for item in CHECKLIST:
        maps_to = item["maps_to"]
        if item["id"] == "script_clearance_review":
            rows.append({**item, "status": "clear",
                         "note": f"This run — {len(assessed)} items researched and graded.",
                         "findings": []})
            continue
        if not maps_to:
            rows.append({**item, "status": "out_of_scope",
                         "note": "Requires executed documents — outside a script scan.",
                         "findings": []})
            continue
        if maps_to in (["TITLE"], ["AI"]):
            # Filled client-side by TitleGuard / the AI intake.
            rows.append({**item, "status": "pending",
                         "note": "Run TitleGuard on the working title." if maps_to == ["TITLE"]
                                 else "Complete the AI-usage intake below.",
                         "findings": []})
            continue
        hits = [a for a in assessed if a["category"] in maps_to]
        flagged = [a for a in hits if a["verdict"] != "CLEAR"]
        if flagged:
            worst = min(flagged, key=lambda a: order.get(a["verdict"], 3))["verdict"]
            rows.append({**item, "status": "flagged", "worst": worst,
                         "note": f"{len(flagged)} of {len(hits)} items need action before binding.",
                         "findings": [{"id": a["id"], "name": a["name"],
                                       "verdict": a["verdict"]} for a in flagged]})
        else:
            note = (f"{len(hits)} items reviewed, no flags."
                    if hits else "No items of this type detected in the script.")
            rows.append({**item, "status": "clear", "note": note, "findings": []})
    return rows


# ---------------------------------------------------------- AI-usage intake

AI_USAGES: dict[str, str] = {
    "ai_voice": "AI-generated voice or narration (including temp voice tracks)",
    "ai_imagery": "AI-generated imagery or VFX (backgrounds, crowds, de-aging)",
    "ai_music": "AI-generated music or score elements",
    "ai_writing": "AI-assisted screenwriting or dialogue generation",
    "ai_likeness": "Digital replica or AI re-creation of a real person's likeness",
}


class AiVerdict(BaseModel):
    verdict: str = Field(description="One of CLEAR, CAUTION, BLOCKED")
    guidance: str = Field(description="Two sentences: insurability impact and the cheapest cure")


AI_ASSESS_PROMPT = """You are an entertainment insurance broker advising a film
producer on E&O insurability in 2026. The production discloses this AI usage:

USAGE: {usage}

CURRENT MARKET EVIDENCE (via Parallel Search — carrier language, broker alerts,
law firm client alerts):
{evidence}

Grade insurability impact: CLEAR (no exclusion concern under current policies),
CAUTION (insurable IF disclosed and documented — specify what to document),
BLOCKED (triggers current AI exclusions or human-authorship failures as-is —
give the cheapest cure). Ground guidance in the evidence."""


async def run_ai_check(usage_ids: list[str]) -> AsyncGenerator[dict[str, Any], None]:
    # Dedupe before fanning out: this endpoint is public and every usage costs a
    # paid search, so a repeated id must not multiply into billed work.
    usages = [(u, AI_USAGES[u]) for u in dict.fromkeys(usage_ids) if u in AI_USAGES]
    if not usages:
        yield {"type": "ai_done"}
        return

    if config.MOCK_GEMINI or config.MOCK_PARALLEL:
        yield {"type": "ai_stage", "status": "start"}
        for uid, label in usages:
            yield {"type": "ai_result", "usage": uid, "label": label,
                   "verdict": "CAUTION",
                   "guidance": "Mock guidance — enable live mode for real carrier research.",
                   "sources": []}
        yield {"type": "ai_done"}
        return

    try:
        yield {"type": "ai_stage", "status": "start"}
        for uid, label in usages:
            resp = await parallel_client.search(
                objective=(
                    f"Find how film/TV producers E&O and media liability policies "
                    f"in 2025-2026 treat this production practice: {label}. "
                    f"Look for AI exclusion riders, disclosure requirements, and "
                    f"human-authorship/copyrightability conditions."
                ),
                search_queries=[
                    "film E&O insurance generative AI exclusion 2026",
                    f"{label} insurance disclosure film production",
                    "media liability AI exclusion human authorship",
                ],
            )
            evidence = parallel_client.evidence_from_response(resp, max_results=5)
            verdict = await _gemini_json(
                AI_ASSESS_PROMPT.format(
                    usage=label,
                    evidence=json.dumps(evidence, indent=1) if evidence else "(none found)",
                ),
                AiVerdict,
            )
            v = verdict.model_dump()
            v["verdict"] = v["verdict"].upper()
            if v["verdict"] not in ("CLEAR", "CAUTION", "BLOCKED"):
                v["verdict"] = "CAUTION"
            yield {"type": "ai_result", "usage": uid, "label": label, **v,
                   "sources": [{"url": e["url"], "title": e["title"]} for e in evidence[:3]]}
        yield {"type": "ai_done"}
    except Exception as exc:
        yield {"type": "error", "message": str(exc)}
```

### `backend/app/mockdata.py`

Mock mode — the full pipeline with canned data and realistic latency, so the product demos with no keys at all.

```python
"""Mock mode for ClearanceRoom (MOCK_MODE=1).

Runs the entire pipeline against MIDNIGHT STATIC with canned data and realistic
latency, so the product demos end-to-end before Vertex/Parallel keys are wired.
The UI shows a MOCK badge whenever this mode is active — never record the
judging demo in mock mode.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

MOCK_ENTITIES: list[dict[str, Any]] = [
    {"id": "e1", "name": "Coca-Cola billboard", "category": "BRAND",
     "scene": "EXT. THE BLUE COMET DINER - NIGHT",
     "context": "A massive COCA-COLA billboard glows red across the street."},
    {"id": "e2", "name": "Ford Crown Victoria", "category": "BRAND",
     "scene": "EXT. THE BLUE COMET DINER - NIGHT",
     "context": "A Ford Crown Victoria idles at the curb."},
    {"id": "e3", "name": "Casablanca (film clip)", "category": "MEDIA",
     "scene": "INT. THE BLUE COMET DINER - CONTINUOUS",
     "context": "A TV above the counter plays the airport scene from CASABLANCA."},
    {"id": "e4", "name": "Nike windbreaker", "category": "BRAND",
     "scene": "INT. THE BLUE COMET DINER - CONTINUOUS",
     "context": "Danny, rain-soaked in a beat-up NIKE windbreaker."},
    {"id": "e5", "name": "Rolex Submariner", "category": "BRAND",
     "scene": "INT. THE BLUE COMET DINER - CONTINUOUS",
     "context": "A Rolex Submariner that looks two owners past legitimate — implied stolen goods."},
    {"id": "e6", "name": "Elvis Presley poster", "category": "ARTWORK",
     "scene": "INT. THE BLUE COMET DINER - CONTINUOUS",
     "context": "A faded ELVIS PRESLEY poster hangs crooked next to the pie case."},
    {"id": "e7", "name": "Taylor Swift", "category": "PERSON",
     "scene": "INT. THE BLUE COMET DINER - CONTINUOUS",
     "context": "Dialogue: 'Thinks she's gonna be bigger than Taylor Swift.'"},
    {"id": "e8", "name": "Hey Jude (sung by character)", "category": "MUSIC",
     "scene": "INT. THE BLUE COMET DINER - CONTINUOUS",
     "context": "Danny sings 'Hey Jude... don't make it bad...' on screen."},
    {"id": "e9", "name": "555-0147 phone number", "category": "OTHER",
     "scene": "INT. THE BLUE COMET DINER - CONTINUOUS",
     "context": "Dialogue: 'Call me back at 555-0147.'"},
    {"id": "e10", "name": "Banksy mural (Girl with Balloon)", "category": "ARTWORK",
     "scene": "EXT. ALLEY BEHIND THE DINER - LATER",
     "context": "The alley wall carries a BANKSY mural — the girl with the red balloon."},
    {"id": "e11", "name": "Chrysler Building", "category": "LOCATION",
     "scene": "EXT. ALLEY BEHIND THE DINER - LATER",
     "context": "Dialogue references meeting at the Chrysler Building observation level."},
    {"id": "e12", "name": "The Blue Comet Diner", "category": "ORGANIZATION",
     "scene": "EXT. THE BLUE COMET DINER - NIGHT",
     "context": "Fictional diner name used as the primary location."},
]

MOCK_EVIDENCE: dict[str, list[dict[str, Any]]] = {
    "e1": [{"url": "https://tsdr.uspto.gov/#caseNumber=coca-cola", "title": "USPTO TSDR — COCA-COLA registered marks",
            "publish_date": None, "excerpts": ["COCA-COLA word and script marks live and registered across multiple classes; The Coca-Cola Company actively polices depictions."]},
           {"url": "https://www.hollywoodreporter.com/business/business-news/brands-product-placement-clearance", "title": "How Studios Clear Brands On Screen",
            "publish_date": "2024-03-11", "excerpts": ["Prominent signage generally requires clearance or greeking unless incidental; Coca-Cola historically cooperative but requires approval of context."]}],
    "e2": [{"url": "https://media.ford.com/content/fordmedia/fna/us/en/legal.html", "title": "Ford Motor Company — media & depiction guidelines",
            "publish_date": None, "excerpts": ["Ford vehicles appearing in ordinary street contexts are commonly treated as incidental use; disparaging or defect-implying depictions require review."]}],
    "e3": [{"url": "https://www.warnerbros.com/company/divisions/clip-and-still-licensing", "title": "Warner Bros. Clip & Still Licensing",
            "publish_date": None, "excerpts": ["Casablanca (1942) clip rights controlled by Warner Bros. Discovery; on-screen playback within another production requires a negotiated clip license."]},
           {"url": "https://www.filmindependent.org/blog/clip-licensing-costs", "title": "What Clip Licensing Actually Costs",
            "publish_date": "2023-09-02", "excerpts": ["Studio-library clips typically license in the $5,000–$25,000 range per minute for festival-and-streaming windows."]}],
    "e4": [{"url": "https://tsdr.uspto.gov/#caseNumber=nike-swoosh", "title": "USPTO TSDR — NIKE / Swoosh registrations",
            "publish_date": None, "excerpts": ["NIKE marks live and famous; famous-mark status raises dilution exposure for unlicensed prominent use."]}],
    "e5": [{"url": "https://www.rolex.com/legal-notices", "title": "Rolex — trademark and legal notices",
            "publish_date": None, "excerpts": ["Rolex enforces aggressively; depiction implying counterfeit or stolen goods materially raises objection risk (tarnishment)."]}],
    "e6": [{"url": "https://www.graceland.com/licensing", "title": "Elvis Presley Enterprises — licensing",
            "publish_date": None, "excerpts": ["Elvis name, image and likeness managed by Authentic Brands Group; poster artwork carries its own separate copyright."]}],
    "e7": [{"url": "https://www.law.cornell.edu/wex/right_of_publicity", "title": "Right of publicity — overview",
            "publish_date": None, "excerpts": ["Passing verbal references to living celebrities in fiction are generally protected expression absent implied endorsement or defamation."]}],
    "e8": [{"url": "https://www.sonymusicpub.com/en/song-catalog", "title": "Sony Music Publishing — catalog (Lennon-McCartney)",
            "publish_date": None, "excerpts": ["Hey Jude publishing administered within Sony Music Publishing; Beatles compositions are among the most expensive sync clearances in the industry."]},
           {"url": "https://variety.com/2019/music/news/beatles-songs-films-sync-licensing", "title": "Why Beatles Songs Rarely Appear in Films",
            "publish_date": "2019-06-14", "excerpts": ["On-camera performance of a Beatles composition requires sync license from publishing; quotes commonly reach six figures — many productions rewrite the scene."]}],
    "e9": [{"url": "https://www.nanpa.com/numbering/555-line-numbers", "title": "NANPA — 555 line numbers for fictional use",
            "publish_date": None, "excerpts": ["555-0100 through 555-0199 are reserved for fictional use in the North American Numbering Plan."]}],
    "e10": [{"url": "https://www.pestcontroloffice.com", "title": "Pest Control — authentication body for Banksy",
             "publish_date": None, "excerpts": ["Banksy works are protected by copyright; Pest Control has pursued unauthorized commercial reproductions. Prominent featured use in film requires license or removal."]}],
    "e11": [{"url": "https://tsdr.uspto.gov/#caseNumber=chrysler-building", "title": "USPTO — Chrysler Building design marks",
             "publish_date": None, "excerpts": ["The building's distinctive spire design is registered; incidental skyline use is customary, featured plot use warrants E&O review."]}],
    "e12": [{"url": "https://tmsearch.uspto.gov/search?q=blue+comet+diner", "title": "USPTO search — 'Blue Comet Diner'",
             "publish_date": None, "excerpts": ["No live registrations found for 'Blue Comet Diner' in restaurant services; nearest matches inactive or unrelated classes."]}],
}

MOCK_ASSESSMENTS: dict[str, dict[str, Any]] = {
    "e1": {"verdict": "CAUTION", "risk_score": 55,
           "rationale": "Famous registered mark shown as prominent set signage, not incidental background. Coca-Cola polices context of depictions.",
           "recommendation": "Seek brand approval, or greek the billboard to a fictional cola. If kept, document incidental framing for E&O."},
    "e2": {"verdict": "CLEAR", "risk_score": 12,
           "rationale": "Ordinary street depiction of a common vehicle, non-disparaging and incidental. Standard industry practice treats this as cleared.",
           "recommendation": "No action. Note incidental-use rationale in the clearance log."},
    "e3": {"verdict": "BLOCKED", "risk_score": 92,
           "rationale": "On-screen playback of a Warner Bros. library film, including recognizable audio dialogue, is unlicensable without a negotiated clip license.",
           "recommendation": "Obtain WB clip license (budget $5k–$25k/min) or replace with public-domain or original footage."},
    "e4": {"verdict": "CAUTION", "risk_score": 48,
           "rationale": "Famous mark worn by the lead in multiple scenes. Wardrobe logos are a classic E&O flag when featured rather than incidental.",
           "recommendation": "Remove or tape the swoosh, or use a cleared wardrobe alternative. Nike rarely approves gritty-context uses."},
    "e5": {"verdict": "CAUTION", "risk_score": 62,
           "rationale": "Script implies the watch is stolen ('two owners past legitimate') — tarnishment risk on an aggressively enforced luxury mark.",
           "recommendation": "Cut the brand name from action lines; shoot a generic dive watch. Do not show the crown logo in insert shots."},
    "e6": {"verdict": "CAUTION", "risk_score": 58,
           "rationale": "Two stacked rights: poster artwork copyright and Elvis likeness rights (ABG). Featured set dressing, clearly identifiable.",
           "recommendation": "License via ABG/EPE or swap for cleared/original artwork. Fictional 'king of rock' pastiche is a safe fallback."},
    "e7": {"verdict": "CLEAR", "risk_score": 20,
           "rationale": "Single verbal reference to a living celebrity, non-defamatory, no implied endorsement. Protected expressive use.",
           "recommendation": "No action. Keep dialogue non-disparaging in future drafts to preserve the analysis."},
    "e8": {"verdict": "BLOCKED", "risk_score": 95,
           "rationale": "On-camera performance of a Lennon-McCartney composition requires sync clearance; Beatles catalog quotes routinely reach six figures.",
           "recommendation": "Rewrite the moment (hum an original melody, or license an affordable alternative). Do not shoot as written."},
    "e9": {"verdict": "CLEAR", "risk_score": 2,
           "rationale": "555-0147 falls in the NANPA range reserved for fiction (555-0100 to 555-0199).",
           "recommendation": "No action."},
    "e10": {"verdict": "BLOCKED", "risk_score": 88,
            "rationale": "Copyrighted artwork featured as a deliberate story beat ('the Banksy girl reaches for her balloon'). Pest Control enforces commercial reproduction.",
            "recommendation": "Commission original street art for the location, or license. Featured framing makes fair-use defense weak."},
    "e11": {"verdict": "CAUTION", "risk_score": 44,
            "rationale": "Named as a plot destination; the building's spire design is a registered mark. Skyline glimpses are customary, featured use needs review.",
            "recommendation": "Fine for dialogue reference; if shooting exteriors as a featured location, route through E&O counsel and location agreements."},
    "e12": {"verdict": "CLEAR", "risk_score": 8,
            "rationale": "Fictional business name with no live conflicting registrations in relevant classes.",
            "recommendation": "No action. Archive the USPTO search snapshot in the clearance log."},
}

MOCK_REPORT = (
    "MIDNIGHT STATIC presents a moderate-to-high clearance load for a short script: "
    "12 flagged items — 4 clear, 5 caution, 3 blocked. The three blockers are structural: "
    "an on-camera Beatles performance (Hey Jude), a Warner Bros. clip playing on set "
    "(Casablanca), and a featured Banksy mural used as a story beat. Each has a workable "
    "creative substitution that removes six-figure licensing exposure. The caution tier is "
    "dominated by famous-brand set dressing and wardrobe (Coca-Cola, Nike, Rolex, Elvis "
    "poster) — standard greeking and wardrobe swaps resolve all four without story impact. "
    "Recommend a revised draft before scheduling: total exposure drops from an estimated "
    "$150k+ in licensing to near zero with substitutions, and the flagged obstacles to "
    "E&O review are removed — counsel sign-off still required."
)


async def mock_breakdown(script_text: str) -> list[dict[str, Any]]:
    await asyncio.sleep(2.2)
    return [dict(e) for e in MOCK_ENTITIES]


async def mock_search(objective: str) -> dict[str, Any]:
    await asyncio.sleep(random.uniform(0.9, 2.4))
    for eid, entity in ((e["id"], e) for e in MOCK_ENTITIES):
        token = entity["name"].split(" (")[0].lower()
        if token.split()[0] in objective.lower():
            return {"search_id": f"mock-{eid}", "results": MOCK_EVIDENCE.get(eid, [])}
    return {"search_id": "mock-generic", "results": []}


async def mock_assess(entity: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    await asyncio.sleep(random.uniform(0.4, 1.1))
    return dict(MOCK_ASSESSMENTS[entity["id"]])


async def mock_report(assessed: list[dict[str, Any]]) -> str:
    await asyncio.sleep(2.0)
    return MOCK_REPORT


async def mock_dossier(entity: dict[str, Any]):
    """Canned Deep Dossier so the Task API lane demos without keys."""
    yield {"type": "dossier_stage", "status": "submitted", "processor": "core",
           "item": entity["name"]}
    await asyncio.sleep(1.0)
    yield {"type": "dossier_stage", "status": "running", "run_id": "trun_mock"}
    for i in range(3):
        await asyncio.sleep(1.2)
        yield {"type": "dossier_tick", "status": "running", "elapsed": (i + 1) * 5}
    yield {
        "type": "dossier_result", "item": entity["name"], "source_count": 6,
        "fields": [
            {"field": "rights_holders", "label": "rights holders",
             "value": "Mock dossier — enable live mode for real deep research.",
             "confidence": "medium", "reasoning": "Mock reasoning.",
             "citations": [{"title": "Mock source", "url": "https://example.com"}]},
        ],
    }


async def mock_title_check(title: str):
    """Canned TitleGuard flow so the UI demos without keys."""
    yield {"type": "tg_stage", "stage": "sweep", "status": "start"}
    await asyncio.sleep(1.5)
    yield {"type": "tg_stage", "stage": "sweep", "status": "done", "searches": 2, "sources": 7}
    yield {"type": "tg_stage", "stage": "assess", "status": "start"}
    await asyncio.sleep(1.2)
    yield {
        "type": "tg_verdict", "title": title, "verdict": "CAUTION", "risk_score": 45,
        "conflicts": [
            {"name": f"{title} (podcast)", "medium": "podcast", "year": "2023",
             "url": "https://example.com/mock", "severity": "MEDIUM"},
        ],
        "rationale": "Mock verdict — enable live mode for a real sweep.",
        "recommendation": "Run live TitleGuard before marketing under this title.",
    }
    yield {"type": "tg_stage", "stage": "assess", "status": "done"}
    yield {"type": "tg_stage", "stage": "alternates", "status": "start"}
    for alt in (f"{title} AFTER DARK", f"DEAD AIR", f"THE {title.split()[0]} HOUR"):
        await asyncio.sleep(0.8)
        yield {"type": "tg_alternate", "title": alt, "verdict": "CLEAR",
               "note": "Mock screen — no conflicts in canned data."}
    yield {"type": "tg_stage", "stage": "alternates", "status": "done"}
    yield {"type": "tg_done"}
```

### `backend/app/main.py`

The API. One hardened SSE wrapper for every streaming route: bounded concurrency, keepalive heartbeats, and static serving in production.

```python
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


class DossierRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=40)
    context: str = Field(min_length=1, max_length=1000)


@app.post("/api/dossier")
async def dossier(req: DossierRequest) -> StreamingResponse:
    from .dossier import run_dossier

    return _sse(run_dossier(req.model_dump()))


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
```

### `backend/tests/test_clearance.py`

15 tests, no network required — including a full pipeline run streamed through the real SSE endpoint.

```python
"""Tests for the deterministic parts of the clearance pipeline.

Everything here runs without network or credentials: the pure mapping/grading
logic, and a full pipeline run in mock mode through the real SSE endpoint.
"""
import json
import os

os.environ.setdefault("MOCK_MODE", "1")

import pytest
from fastapi.testclient import TestClient

from app import config, parallel_client
from app.eobinder import CHECKLIST, build_checklist
from app.main import app
from app.pipeline import PRECEDENTS, VERDICTS, _fallback_summary

client = TestClient(app)


# --------------------------------------------------------------- evidence

def test_evidence_from_response_flattens_and_truncates():
    resp = {
        "results": [
            {"url": f"https://example.com/{i}", "title": f"t{i}",
             "publish_date": None, "excerpts": ["a", "b", "c", "d"]}
            for i in range(9)
        ]
    }
    ev = parallel_client.evidence_from_response(resp, max_results=5)
    assert len(ev) == 5
    assert all(len(e["excerpts"]) <= 3 for e in ev)
    assert ev[0]["url"] == "https://example.com/0"


def test_evidence_from_empty_response():
    assert parallel_client.evidence_from_response({}) == []
    assert parallel_client.evidence_from_response({"results": []}) == []


# --------------------------------------------------------------- E&O binder

def test_checklist_has_twelve_rows_each_with_a_source():
    assert len(CHECKLIST) == 12
    assert all(row["source_url"].startswith("http") for row in CHECKLIST)
    assert len({row["id"] for row in CHECKLIST}) == 12


def test_build_checklist_maps_findings_to_the_right_rows():
    assessed = [
        {"id": "e1", "name": "Hey Jude", "category": "MUSIC", "verdict": "BLOCKED"},
        {"id": "e2", "name": "Ford", "category": "BRAND", "verdict": "CLEAR"},
        {"id": "e3", "name": "Casablanca", "category": "MEDIA", "verdict": "CAUTION"},
    ]
    rows = {r["id"]: r for r in build_checklist(assessed)}

    assert rows["music_licenses"]["status"] == "flagged"
    assert rows["music_licenses"]["worst"] == "BLOCKED"
    assert rows["clip_stock_footage_licenses"]["worst"] == "CAUTION"
    # BRAND is CLEAR and it is the only item feeding location_property_releases
    assert rows["location_property_releases"]["status"] == "clear"
    # Rows needing executed documents are never auto-cleared by a script scan
    assert rows["copyright_chain_of_title"]["status"] == "out_of_scope"
    assert rows["creator_agreements_all_media_rights"]["status"] == "out_of_scope"
    # Rows filled by other flows stay pending until those flows run
    assert rows["title_report"]["status"] == "pending"
    assert rows["ai_usage_disclosure"]["status"] == "pending"


def test_build_checklist_worst_verdict_wins_within_a_row():
    assessed = [
        {"id": "a", "name": "x", "category": "MUSIC", "verdict": "CAUTION"},
        {"id": "b", "name": "y", "category": "MUSIC", "verdict": "BLOCKED"},
    ]
    row = {r["id"]: r for r in build_checklist(assessed)}["music_licenses"]
    assert row["worst"] == "BLOCKED"
    assert len(row["findings"]) == 2


def test_build_checklist_handles_no_findings():
    rows = build_checklist([])
    assert len(rows) == 12
    assert all(r["status"] != "flagged" for r in rows)


# --------------------------------------------------------------- precedents

def test_every_precedent_is_a_real_citation():
    assert PRECEDENTS
    for category, card in PRECEDENTS.items():
        assert card["url"].startswith("https://"), category
        assert len(card["case"]) > 20, category


# ----------------------------------------------------------- fallback report

def test_fallback_summary_counts_and_names_blockers():
    summary = _fallback_summary([
        {"name": "Hey Jude", "verdict": "BLOCKED", "risk_score": 95},
        {"name": "Banksy mural", "verdict": "BLOCKED", "risk_score": 88},
        {"name": "Ford", "verdict": "CLEAR", "risk_score": 10},
    ])
    assert "2 blocked" in summary
    assert "Hey Jude" in summary and "Banksy mural" in summary


# ------------------------------------------------------------------- API

def test_health_reports_models_and_limits():
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert set(body["models"]) == {"breakdown", "assess", "report"}
    assert body["limits"]["max_script_chars"] > 0


@pytest.mark.parametrize("mode,expected", [
    ("clearance", "MIDNIGHT STATIC"),
    ("truestory", "STATIC & LIGHTNING"),
])
def test_sample_scripts_are_served(mode, expected):
    body = client.get(f"/api/sample?mode={mode}").json()
    assert body["title"] == expected
    assert len(body["script"]) > 500


def test_run_rejects_empty_and_oversized_scripts():
    assert client.post("/api/clearance/run", json={"script": ""}).status_code == 422
    huge = "x" * (config.MAX_SCRIPT_CHARS + 1)
    assert client.post("/api/clearance/run", json={"script": huge}).status_code == 422


def _collect_sse(path: str, payload: dict) -> list[dict]:
    events = []
    with client.stream("POST", path, json=payload) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


@pytest.mark.skipif(not config.MOCK_MODE, reason="mock mode only")
def test_full_mock_run_streams_a_complete_report():
    script = client.get("/api/sample").json()["script"]
    events = _collect_sse("/api/clearance/run", {"script": script})
    kinds = [e["type"] for e in events]

    assert "error" not in kinds
    assert kinds[-1] == "done"

    found = [e for e in events if e["type"] == "entity_found"]
    results = [e for e in events if e["type"] == "entity_result"]
    assert len(found) == len(results) > 0

    report = next(e for e in events if e["type"] == "report")
    assert sum(report["stats"].values()) == len(results)
    assert set(report["stats"]) == set(VERDICTS)
    assert len(report["eo_checklist"]) == 12
    assert report["summary"]

    # Every non-CLEAR finding carries a precedent card where one is defined
    for r in results:
        if r["verdict"] != "CLEAR" and r.get("precedent"):
            assert r["precedent"]["url"].startswith("https://")


@pytest.mark.skipif(not config.MOCK_MODE, reason="mock mode only")
def test_stages_run_in_fixed_order():
    script = client.get("/api/sample").json()["script"]
    events = _collect_sse("/api/clearance/run", {"script": script})
    starts = [e["stage"] for e in events
              if e["type"] == "stage" and e["status"] == "start"]
    assert starts == ["breakdown", "research", "assess", "report"]


@pytest.mark.skipif(not config.MOCK_MODE, reason="mock mode only")
def test_ai_check_dedupes_repeated_usages():
    events = _collect_sse("/api/eo/ai-check",
                          {"usages": ["ai_voice", "ai_voice", "ai_voice", "bogus"]})
    results = [e for e in events if e["type"] == "ai_result"]
    assert len(results) == 1
    assert results[0]["usage"] == "ai_voice"
```


## Frontend — the war room

React + Vite + Tailwind v4. Every stage renders as it streams.

### `web/src/App.tsx`

The board: mode switching, SSE event handling, the live search ticker, and the accessible run-status live region.

```tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  Entity,
  EntityResult,
  EntityStatus,
  PipelineEvent,
  Report,
  StageKey,
  StageStatus,
  TitleVerdict,
} from './types'
import { StageRail } from './components/StageRail'
import { EntityCard } from './components/EntityCard'
import { ReportPanel } from './components/ReportPanel'
import { TitleGuard } from './components/TitleGuard'
import { streamSSE } from './lib/stream'

const INITIAL_STAGES: Record<StageKey, StageStatus> = {
  breakdown: 'idle',
  research: 'idle',
  assess: 'idle',
  report: 'idle',
}

type Mode = 'clearance' | 'truestory'

const MODES: Record<Mode, {
  label: string
  tagline: string
  endpoint: string
  button: string
  itemsLabel: string
  reportTitle: string
}> = {
  clearance: {
    label: '🎬 Script Clearance',
    tagline: 'brands · music · artwork · clips · locations',
    endpoint: '/api/clearance/run',
    button: '🎬 RUN CLEARANCE',
    itemsLabel: '🎞 clearance items',
    reportTitle: 'CLEARANCE TRIAGE REPORT',
  },
  truestory: {
    label: '⚖️ True-Story Shield',
    tagline: 'defamation fact-check for “based on a true story”',
    endpoint: '/api/truestory/run',
    button: '⚖️ FACT-CHECK SCRIPT',
    itemsLabel: '⚖ factual claims',
    reportTitle: 'DEFAMATION EXPOSURE TRIAGE',
  },
}

export default function App() {
  const [mode, setMode] = useState<Mode>('clearance')
  const [script, setScript] = useState('')
  const [title, setTitle] = useState('UNTITLED SCRIPT')
  const [running, setRunning] = useState(false)
  // null = health check hasn't resolved; never claim "live" before we know.
  const [mock, setMock] = useState<{ gemini: boolean; parallel: boolean } | null>(null)
  const [stages, setStages] = useState(INITIAL_STAGES)
  const [entities, setEntities] = useState<Entity[]>([])
  const [statuses, setStatuses] = useState<Record<string, EntityStatus>>({})
  const [results, setResults] = useState<Record<string, EntityResult>>({})
  const [report, setReport] = useState<Report | null>(null)
  const [ticker, setTicker] = useState<{ searches: number; sources: number } | null>(null)
  const [tgVerdict, setTgVerdict] = useState<TitleVerdict | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Research/assess failures the backend emits as `warning` frames. Previously
  // unhandled, so a failed live search vanished with no signal on screen.
  const [warnings, setWarnings] = useState<string[]>([])
  const boardRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetch(`/api/sample?mode=${mode}`)
      .then((r) => r.json())
      .then((d) => {
        setScript(d.script)
        setTitle(d.title)
        setEntities([])
        setStatuses({})
        setResults({})
        setReport(null)
        setTicker(null)
        setTgVerdict(null)
        setStages(INITIAL_STAGES)
        setWarnings([])
      })
      .catch(() => setError('Backend not reachable — is the API server running?'))
  }, [mode])

  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json())
      .then((d) => setMock({ gemini: d.mock_gemini, parallel: d.mock_parallel }))
      .catch(() => setMock({ gemini: true, parallel: true }))
  }, [])

  const handleEvent = useCallback((ev: PipelineEvent) => {
    switch (ev.type) {
      case 'stage':
        setStages((s) => ({ ...s, [ev.stage]: ev.status === 'start' ? 'running' : 'done' }))
        break
      case 'entity_found':
        setEntities((es) => [...es, ev.entity])
        setStatuses((s) => ({ ...s, [ev.entity.id]: 'queued' }))
        break
      case 'entity_status':
        setStatuses((s) => ({ ...s, [ev.id]: ev.status }))
        break
      case 'entity_result': {
        const { type: _t, id, ...result } = ev
        setStatuses((s) => ({ ...s, [id]: 'done' }))
        setResults((r) => ({ ...r, [id]: result as EntityResult }))
        break
      }
      case 'report': {
        const { type: _t, ...rep } = ev
        setReport(rep as Report)
        break
      }
      case 'ticker':
        setTicker({ searches: ev.searches, sources: ev.sources })
        break
      case 'warning':
        setWarnings((w) => (w.includes(ev.message) ? w : [...w, ev.message]))
        break
      case 'done':
        setRunning(false)
        break
      case 'error':
        setError(ev.message)
        setRunning(false)
        break
    }
  }, [])

  const run = useCallback(async () => {
    setRunning(true)
    setError(null)
    setStages(INITIAL_STAGES)
    setEntities([])
    setStatuses({})
    setResults({})
    setReport(null)
    setTicker(null)
    setWarnings([])
    boardRef.current?.scrollIntoView({ behavior: 'smooth' })

    try {
      await streamSSE<PipelineEvent>(MODES[mode].endpoint, { script }, handleEvent)
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }, [script, mode, handleEvent])

  const doneCount = Object.values(statuses).filter((s) => s === 'done').length

  return (
    <div className="grain min-h-screen">
      {/* Header */}
      <header className="border-b border-stone-800 bg-stone-950/80 backdrop-blur sticky top-0 z-40">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-y-1 px-6 py-3">
          <div className="flex items-baseline gap-3">
            <h1 className="font-display text-3xl tracking-wide text-amber-400">
              CLEARANCE<span className="text-stone-100">ROOM</span>
            </h1>
            <span className="hidden font-mono text-[11px] uppercase tracking-[0.25em] text-stone-500 sm:inline">
              every frame cleared
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-3 font-mono text-[11px] uppercase tracking-widest">
            {mock?.gemini && (
              <span className="rounded border border-fuchsia-500/50 px-2 py-0.5 text-fuchsia-400">
                gemini mock
              </span>
            )}
            {mock === null ? (
              <span className="rounded border border-stone-600 px-2 py-0.5 text-stone-400">
                checking…
              </span>
            ) : mock.parallel ? (
              <span className="rounded border border-fuchsia-500/50 px-2 py-0.5 text-fuchsia-400">
                parallel mock
              </span>
            ) : (
              <span className="rounded border border-emerald-500/50 px-2 py-0.5 text-emerald-400">
                parallel live
              </span>
            )}
            {running && (
              <span className="flex items-center gap-1.5 text-red-400">
                <span className="blink inline-block h-2 w-2 rounded-full bg-red-500" /> rec
              </span>
            )}
            <span className="hidden text-stone-500 md:inline">gemini × parallel</span>
          </div>
        </div>
      </header>

      {/* Screen readers get no signal from the streaming board otherwise. */}
      <div aria-live="polite" className="sr-only">
        {running
          ? `Running. ${doneCount} of ${entities.length} items assessed.`
          : report
            ? `Run complete. ${report.stats.BLOCKED} blocked, ${report.stats.CAUTION} caution, ${report.stats.CLEAR} clear.`
            : ''}
      </div>

      <main className="mx-auto max-w-7xl px-6 py-8">
        <p className="mb-6 max-w-3xl text-[14px] leading-relaxed text-stone-400">
          Script clearance in minutes, not weeks. A deterministic Gemini agent breaks your
          screenplay into clearable items, researches each one on the live web with Parallel
          Search, and hands your counsel a cited triage report mapped to the E&amp;O checklist.
        </p>
        {/* Mode switch */}
        <div className="mb-6 flex flex-wrap items-center gap-2">
          {(Object.keys(MODES) as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              disabled={running}
              aria-pressed={mode === m}
              className={[
                'rounded-lg border px-4 py-2 text-left transition disabled:cursor-not-allowed disabled:opacity-50',
                mode === m
                  ? 'border-amber-400/70 bg-amber-400/10'
                  : 'border-stone-800 bg-stone-900/40 hover:border-stone-700',
              ].join(' ')}
            >
              <div
                className={`font-display text-xl tracking-wide ${mode === m ? 'text-amber-400' : 'text-stone-300'}`}
              >
                {MODES[m].label}
              </div>
              <div className="font-mono text-[10px] uppercase tracking-widest text-stone-400">
                {MODES[m].tagline}
              </div>
            </button>
          ))}
        </div>

        <div className="grid gap-8 lg:grid-cols-[minmax(320px,2fr)_3fr]">
          {/* Script panel */}
          <section>
            <div className="mb-2 flex items-center justify-between">
              <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-stone-400">
                📄 the script — {title}
              </h2>
              <span className="font-mono text-[10px] text-stone-600">
                {script.split('\n').length} lines
              </span>
            </div>
            <textarea
              value={script}
              onChange={(e) => setScript(e.target.value)}
              spellCheck={false}
              aria-label="Screenplay text to analyze"
              className="h-[420px] w-full resize-none rounded-lg border border-stone-800 bg-stone-900/60 p-4 font-mono text-[12px] leading-relaxed text-stone-300 outline-none focus:border-amber-500/50"
            />
            <button
              onClick={run}
              disabled={running || !script.trim()}
              className="mt-3 w-full rounded-lg bg-amber-400 py-3 font-display text-2xl tracking-wider text-stone-950 transition hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {running ? 'ROLLING…' : MODES[mode].button}
            </button>
            {error && (
              <p
                role="alert"
                className="mt-3 rounded border border-red-500/40 bg-red-950/40 p-3 font-mono text-xs text-red-300"
              >
                {error}
              </p>
            )}
            {mode === 'clearance' && (
              <TitleGuard key={title} initialTitle={title} onVerdict={setTgVerdict} />
            )}
          </section>

          {/* Pipeline board */}
          <section ref={boardRef}>
            <StageRail stages={stages} mode={mode} />
            {warnings.length > 0 && (
              <div className="mt-3 rounded border border-amber-500/40 bg-amber-950/30 p-3 font-mono text-[11px] leading-snug text-amber-300">
                {warnings.map((w, i) => (
                  <p key={i}>⚠ {w}</p>
                ))}
              </div>
            )}
            {entities.length > 0 && (
              <div className="mb-3 mt-6 flex items-center justify-between">
                <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-stone-400">
                  {MODES[mode].itemsLabel}
                </h2>
                <span className="font-mono text-[10px] text-stone-500">
                  {ticker && (
                    <span className={running ? 'pulse-soft mr-4 text-sky-400' : 'mr-4 text-sky-400'}>
                      🔍 {ticker.searches} parallel searches · {ticker.sources} sources
                    </span>
                  )}
                  {doneCount}/{entities.length} assessed
                </span>
              </div>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              {entities.map((e) => (
                <EntityCard
                  key={e.id}
                  entity={e}
                  status={statuses[e.id] ?? 'queued'}
                  result={results[e.id]}
                />
              ))}
            </div>
            {entities.length === 0 && running && (
              <div className="mt-6 rounded-lg border border-stone-800 p-10 text-center">
                <p className="pulse-soft font-display text-2xl tracking-wide text-amber-400">
                  {mode === 'truestory'
                    ? 'GEMINI IS READING FOR CLAIMS…'
                    : 'GEMINI IS BREAKING DOWN THE SCRIPT…'}
                </p>
                <p className="mt-1 font-mono text-xs text-stone-500">
                  clearable items land here as they're found
                </p>
              </div>
            )}
            {entities.length === 0 && !running && (
              <div className="mt-6 rounded-lg border border-dashed border-stone-800 p-10 text-center">
                <p className="font-display text-2xl tracking-wide text-stone-600">
                  THE BOARD IS DARK
                </p>
                <p className="mt-1 font-mono text-xs text-stone-600">
                  run clearance to light it up
                </p>
                <div className="mx-auto mt-5 grid max-w-md gap-1.5 text-left font-mono text-[11px] text-stone-500">
                  <p>01 · gemini extracts every clearable item from the script</p>
                  <p>02 · parallel search researches each one on the live web</p>
                  <p>03 · gemini grades risk · you get an e&amp;o-ready report</p>
                </div>
              </div>
            )}
          </section>
        </div>

        {report && (
          <ReportPanel
            report={report}
            title={title}
            heading={MODES[mode].reportTitle}
            titleVerdict={tgVerdict}
          />
        )}
      </main>

      <footer className="border-t border-stone-800 py-4 text-center font-mono text-[10px] uppercase tracking-widest text-stone-600">
        clearanceroom · gemini on vertex ai · google adk · parallel search api
      </footer>
    </div>
  )
}
```

### `web/src/types.ts`

The event protocol, shared by the backend's SSE frames and every component.

```ts
export type Verdict = 'CLEAR' | 'CAUTION' | 'BLOCKED'
export type EntityStatus = 'queued' | 'researching' | 'assessing' | 'done'
export type StageKey = 'breakdown' | 'research' | 'assess' | 'report'
export type StageStatus = 'idle' | 'running' | 'done'

export interface Entity {
  id: string
  name: string
  category: string
  scene: string
  context: string
}

export interface Source {
  url: string
  title: string
}

export interface Precedent {
  case: string
  url: string
}

export interface EntityResult {
  verdict: Verdict
  risk_score: number
  rationale: string
  recommendation: string
  sources: Source[]
  precedent?: Precedent | null
}

export interface EoFinding {
  id: string
  name: string
  verdict: Verdict
}

export type EoStatus = 'clear' | 'flagged' | 'pending' | 'out_of_scope'

export interface EoRow {
  id: string
  title: string
  requirement: string
  source_url: string
  status: EoStatus
  worst?: Verdict
  note: string
  findings: EoFinding[]
}

export interface Report {
  summary: string
  stats: Record<Verdict, number>
  items: (Entity & EntityResult)[]
  eo_checklist?: EoRow[]
  elapsed_seconds?: number
  searches?: number
  sources?: number
}

export interface AiResult {
  usage: string
  label: string
  verdict: Verdict
  guidance: string
  sources: Source[]
}

export type AiCheckEvent =
  | { type: 'ai_stage'; status: string }
  | ({ type: 'ai_result' } & AiResult)
  | { type: 'ai_done' }
  | { type: 'error'; message: string }

export type PipelineMode = 'clearance' | 'truestory'

export interface DossierField {
  field: string
  label: string
  value: string
  confidence: 'high' | 'medium' | 'low' | 'unknown'
  reasoning: string
  citations: Source[]
}

export type DossierEvent =
  | { type: 'dossier_stage'; status: string; processor?: string; run_id?: string; item?: string }
  | { type: 'dossier_tick'; status: string; elapsed: number }
  | { type: 'dossier_result'; item: string; fields: DossierField[]; source_count: number }
  | { type: 'error'; message: string }

export interface TitleConflict {
  name: string
  medium: string
  year: string
  url: string
  severity: string
}

export interface TitleVerdict {
  title: string
  verdict: Verdict
  risk_score: number
  conflicts: TitleConflict[]
  rationale: string
  recommendation: string
}

export interface TitleAlternate {
  title: string
  verdict: Verdict
  note: string
}

export type TitleGuardEvent =
  | { type: 'tg_stage'; stage: 'sweep' | 'assess' | 'alternates'; status: 'start' | 'done'; searches?: number; sources?: number }
  | ({ type: 'tg_verdict' } & TitleVerdict)
  | ({ type: 'tg_alternate' } & TitleAlternate)
  | { type: 'tg_done' }
  | { type: 'error'; message: string }

export type PipelineEvent =
  | { type: 'stage'; stage: StageKey; status: 'start' | 'done'; count?: number }
  | { type: 'entity_found'; entity: Entity }
  | { type: 'entity_status'; id: string; status: 'researching' | 'assessing' }
  | ({ type: 'entity_result'; id: string } & EntityResult)
  | ({ type: 'report' } & Report)
  | { type: 'ticker'; searches: number; sources: number }
  | { type: 'warning'; id: string; message: string }
  | { type: 'done'; mock: boolean }
  | { type: 'error'; message: string }
```

### `web/src/lib/stream.ts`

The SSE reader. Tolerates CRLF framing and skips an unparseable frame rather than killing a run.

```ts
export async function streamSSE<E>(
  url: string,
  body: unknown,
  onEvent: (event: E) => void,
): Promise<void> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok || !resp.body) throw new Error(`API ${resp.status}`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // Split on CRLF as well as LF: a proxy that rewrites line endings would
    // otherwise never produce a frame boundary and the run would hang.
    const chunks = buffer.split(/\r?\n\r?\n/)
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const match = /^data: ?(.*)$/s.exec(chunk.trim())
      if (!match) continue // ": keepalive" comment frames land here
      try {
        onEvent(JSON.parse(match[1]) as E)
      } catch {
        // One unparseable frame must not kill a run that is otherwise fine.
      }
    }
  }
}
```

### `web/src/lib/url.ts`

Guards `new URL()` on search-supplied URLs — a schemeless one thrown during render takes the whole board down.

```ts
/** Source URLs come from live search results, so they can be schemeless or
 *  malformed. `new URL()` throws on those, and thrown during render it takes
 *  the whole board down — hence the guard. */
export function safeHostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url.replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0] || url
  }
}
```

### `web/src/components/StageRail.tsx`

The four-stage progress rail.

```tsx
import type { PipelineMode, StageKey, StageStatus } from '../types'

const STAGES: { key: StageKey; label: string; engine: string }[] = [
  { key: 'breakdown', label: 'Breakdown', engine: 'Gemini · ADK' },
  { key: 'research', label: 'Research', engine: 'Parallel Search' },
  { key: 'assess', label: 'Assessment', engine: 'Gemini' },
  { key: 'report', label: 'Report', engine: 'Gemini · ADK' },
]

export function StageRail({
  stages,
  mode = 'clearance',
}: {
  stages: Record<StageKey, StageStatus>
  mode?: PipelineMode
}) {
  return (
    <ol className="grid grid-cols-2 items-stretch gap-2 md:flex">
      {STAGES.map((s, i) => {
        const st = stages[s.key]
        return (
          <li key={s.key} className="flex flex-1 items-center gap-2">
            <div
              className={[
                'flex-1 rounded-lg border px-3 py-2 transition-colors',
                st === 'running'
                  ? 'border-amber-400/70 bg-amber-400/10'
                  : st === 'done'
                    ? 'border-emerald-500/40 bg-emerald-500/5'
                    : 'border-stone-800 bg-stone-900/40',
              ].join(' ')}
            >
              <div className="flex items-center gap-2">
                <span
                  className={[
                    'font-mono text-[10px]',
                    st === 'running'
                      ? 'pulse-soft text-amber-400'
                      : st === 'done'
                        ? 'text-emerald-400'
                        : 'text-stone-600',
                  ].join(' ')}
                >
                  {st === 'done' ? '✔' : st === 'running' ? '●' : `0${i + 1}`}
                </span>
                <span
                  className={[
                    'font-display text-lg tracking-wide',
                    st === 'idle' ? 'text-stone-500' : 'text-stone-100',
                  ].join(' ')}
                >
                  {mode === 'truestory' && s.key === 'breakdown' ? 'Claims' : s.label}
                </span>
              </div>
              <div className="font-mono text-[10px] uppercase tracking-widest text-stone-400">
                {s.engine}
              </div>
            </div>
            {i < STAGES.length - 1 && <span className="hidden text-stone-700 md:inline">›</span>}
          </li>
        )
      })}
    </ol>
  )
}
```

### `web/src/components/EntityCard.tsx`

A single clearance finding: verdict stamp, rationale, fix, precedent card, sources, and the Deep Dossier trigger.

```tsx
import type { Entity, EntityResult, EntityStatus } from '../types'
import { safeHostname } from '../lib/url'
import { Dossier } from './Dossier'

const CATEGORY_ICONS: Record<string, string> = {
  BRAND: '🏷',
  PERSON: '🎤',
  MUSIC: '🎵',
  ARTWORK: '🖼',
  LOCATION: '🏙',
  MEDIA: '📺',
  ORGANIZATION: '🏢',
  OTHER: '📎',
  // True-Story Shield claim categories
  CRIMINAL: '⚖',
  PROFESSIONAL: '💼',
  FINANCIAL: '💰',
  RELATIONSHIP: '💍',
  HEALTH: '🏥',
  QUOTE: '💬',
}

const VERDICT_STYLES: Record<string, { stamp: string; bar: string }> = {
  CLEAR: { stamp: 'border-emerald-400 text-emerald-400', bar: 'bg-emerald-400' },
  CAUTION: { stamp: 'border-amber-400 text-amber-400', bar: 'bg-amber-400' },
  BLOCKED: { stamp: 'border-red-500 text-red-500', bar: 'bg-red-500' },
}

export function EntityCard({
  entity,
  status,
  result,
}: {
  entity: Entity
  status: EntityStatus
  result?: EntityResult
}) {
  const v = result ? VERDICT_STYLES[result.verdict] : undefined
  return (
    <article className="card-in relative overflow-hidden rounded-lg border border-stone-800 bg-stone-900/60 p-4">
      {/* risk bar */}
      {result && v && (
        <div
          className={`absolute inset-x-0 top-0 h-0.5 ${v.bar}`}
          style={{ width: `${result.risk_score}%` }}
        />
      )}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm">{CATEGORY_ICONS[entity.category] ?? '📎'}</span>
            <h3 className="truncate font-semibold text-[15px] text-stone-100">{entity.name}</h3>
          </div>
          <p className="mt-0.5 font-mono text-[10px] uppercase tracking-widest text-stone-400">
            {entity.category} · {entity.scene}
          </p>
        </div>
        {result && v && (
          <span
            className={`stamp-in shrink-0 rounded border-2 px-2 py-0.5 font-display text-lg tracking-widest ${v.stamp}`}
          >
            {result.verdict}
          </span>
        )}
      </div>

      <p className="mt-2 text-[12px] italic leading-snug text-stone-400">{entity.context}</p>

      {status === 'queued' && (
        <p className="mt-3 font-mono text-[10px] uppercase tracking-widest text-stone-600">
          ⋯ queued
        </p>
      )}
      {status === 'researching' && (
        <p className="pulse-soft mt-3 font-mono text-[10px] uppercase tracking-widest text-sky-400">
          🔍 researching web evidence · parallel
        </p>
      )}
      {status === 'assessing' && (
        <p className="pulse-soft mt-3 font-mono text-[10px] uppercase tracking-widest text-violet-400">
          ⚖ gemini weighing the evidence
        </p>
      )}

      {result && (
        <div className="mt-3 space-y-2 border-t border-stone-800 pt-3">
          <p className="text-[12px] leading-snug text-stone-300">{result.rationale}</p>
          <p className="text-[12px] leading-snug text-stone-300">
            <span className="font-mono text-[9px] uppercase tracking-widest text-amber-400/80">
              fix ·{' '}
            </span>
            {result.recommendation}
          </p>
          {result.precedent && (
            <p className="rounded border border-red-500/20 bg-red-950/20 px-2 py-1.5 text-[11px] leading-snug text-stone-300">
              <span className="font-mono text-[9px] uppercase tracking-widest text-red-400">
                ⚖ precedent ·{' '}
              </span>
              {result.precedent.case}{' '}
              <a
                href={result.precedent.url}
                target="_blank"
                rel="noreferrer"
                className="font-mono text-[9px] text-sky-500 underline decoration-dotted hover:text-sky-300"
              >
                source
              </a>
            </p>
          )}
          <div className="flex items-center justify-between">
            <div className="flex flex-wrap gap-2">
              {result.sources.slice(0, 3).map((s) => (
                <a
                  key={s.url}
                  href={s.url}
                  target="_blank"
                  rel="noreferrer"
                  title={s.title}
                  className="font-mono text-[9px] text-sky-500 underline decoration-dotted hover:text-sky-300"
                >
                  {safeHostname(s.url)}
                </a>
              ))}
            </div>
            <span className="font-mono text-[10px] text-stone-400">
              risk {result.risk_score}
            </span>
          </div>
          {/* Depth on demand: only flagged items are worth a deep-research run. */}
          {result.verdict !== 'CLEAR' && (
            <Dossier
              name={entity.name}
              category={entity.category}
              context={entity.context}
            />
          )}
        </div>
      )}
    </article>
  )
}
```

### `web/src/components/Dossier.tsx`

Deep Dossier: streams a Parallel Task API run and renders each field with its confidence badge and citations.

```tsx
import { useState } from 'react'
import type { DossierEvent, DossierField } from '../types'
import { streamSSE } from '../lib/stream'
import { safeHostname } from '../lib/url'

const CONFIDENCE_STYLE: Record<string, string> = {
  high: 'border-emerald-500/50 text-emerald-400',
  medium: 'border-amber-400/50 text-amber-400',
  low: 'border-red-500/50 text-red-400',
  unknown: 'border-stone-600 text-stone-400',
}

export function Dossier({
  name,
  category,
  context,
}: {
  name: string
  category: string
  context: string
}) {
  const [open, setOpen] = useState(false)
  const [running, setRunning] = useState(false)
  const [tick, setTick] = useState<{ status: string; elapsed: number } | null>(null)
  const [fields, setFields] = useState<DossierField[] | null>(null)
  const [sourceCount, setSourceCount] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setOpen(true)
    setRunning(true)
    setError(null)
    setFields(null)
    setTick(null)
    try {
      await streamSSE<DossierEvent>('/api/dossier', { name, category, context }, (ev) => {
        switch (ev.type) {
          case 'dossier_tick':
            setTick({ status: ev.status, elapsed: ev.elapsed })
            break
          case 'dossier_result':
            setFields(ev.fields)
            setSourceCount(ev.source_count)
            break
          case 'error':
            setError(ev.message)
            break
        }
      })
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="mt-3 border-t border-stone-800 pt-3">
      {!open && (
        <button
          onClick={run}
          className="w-full rounded border border-sky-500/40 py-1.5 font-mono text-[10px] uppercase tracking-widest text-sky-400 transition hover:bg-sky-500/10"
        >
          🔬 deep dossier · parallel task api
        </button>
      )}

      {open && (
        <>
          <div className="mb-2 flex items-baseline justify-between">
            <span className="font-mono text-[9px] uppercase tracking-widest text-sky-400">
              🔬 deep dossier · parallel task api
            </span>
            {fields && (
              <span className="font-mono text-[9px] text-stone-400">
                {sourceCount} sources
              </span>
            )}
          </div>

          {running && (
            <p className="pulse-soft font-mono text-[10px] uppercase tracking-widest text-sky-400">
              multi-hop research running{tick ? ` · ${tick.elapsed}s` : '…'}
            </p>
          )}

          {error && (
            <p role="alert" className="font-mono text-[10px] text-red-300">
              {error}
            </p>
          )}

          {fields && (
            <dl className="space-y-2.5">
              {fields.map((f) => (
                <div key={f.field} className="card-in">
                  <dt className="flex items-baseline gap-2">
                    <span className="font-mono text-[9px] uppercase tracking-widest text-stone-400">
                      {f.label}
                    </span>
                    <span
                      className={`rounded border px-1.5 font-mono text-[8px] uppercase tracking-widest ${
                        CONFIDENCE_STYLE[f.confidence] ?? CONFIDENCE_STYLE.unknown
                      }`}
                      title={f.reasoning}
                    >
                      {f.confidence}
                    </span>
                  </dt>
                  <dd className="mt-0.5 text-[12px] leading-snug text-stone-300">
                    {f.value}
                    {f.citations.length > 0 && (
                      <span className="ml-1">
                        {f.citations.map((c) => (
                          <a
                            key={c.url}
                            href={c.url}
                            target="_blank"
                            rel="noreferrer"
                            title={c.title}
                            className="ml-1 font-mono text-[9px] text-sky-500 underline decoration-dotted hover:text-sky-300"
                          >
                            {safeHostname(c.url)}
                          </a>
                        ))}
                      </span>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </>
      )}
    </div>
  )
}
```

### `web/src/components/TitleGuard.tsx`

The working-title check, with cleared alternates screened live.

```tsx
import { useState } from 'react'
import type { TitleAlternate, TitleGuardEvent, TitleVerdict } from '../types'
import { streamSSE } from '../lib/stream'

const STAMP: Record<string, string> = {
  CLEAR: 'border-emerald-400 text-emerald-400',
  CAUTION: 'border-amber-400 text-amber-400',
  BLOCKED: 'border-red-500 text-red-500',
}

const DOT: Record<string, string> = {
  CLEAR: 'bg-emerald-400',
  CAUTION: 'bg-amber-400',
  BLOCKED: 'bg-red-500',
}

const STAGE_LABEL: Record<string, string> = {
  sweep: 'sweeping registrations + open web · parallel',
  assess: 'gemini scoring likelihood of confusion',
  alternates: 'screening cleared alternates',
}

export function TitleGuard({
  initialTitle,
  onVerdict,
}: {
  initialTitle: string
  onVerdict?: (v: TitleVerdict) => void
}) {
  const [title, setTitle] = useState(initialTitle)
  const [running, setRunning] = useState(false)
  const [stage, setStage] = useState<string | null>(null)
  const [verdict, setVerdict] = useState<TitleVerdict | null>(null)
  const [alternates, setAlternates] = useState<TitleAlternate[]>([])
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setRunning(true)
    setError(null)
    setVerdict(null)
    setAlternates([])
    try {
      await streamSSE<TitleGuardEvent>('/api/title/check', { title }, (ev) => {
        switch (ev.type) {
          case 'tg_stage':
            setStage(ev.status === 'start' ? ev.stage : null)
            break
          case 'tg_verdict': {
            const { type: _t, ...v } = ev
            setVerdict(v as TitleVerdict)
            onVerdict?.(v as TitleVerdict)
            break
          }
          case 'tg_alternate': {
            const { type: _t, ...a } = ev
            setAlternates((prev) => [...prev, a as TitleAlternate])
            break
          }
          case 'error':
            setError(ev.message)
            break
        }
      })
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
      setStage(null)
    }
  }

  return (
    <section className="mt-6 rounded-lg border border-stone-800 bg-stone-900/60 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-stone-400">
          🛡 titleguard — working title check
        </h2>
        <span className="font-mono text-[10px] uppercase tracking-widest text-stone-400">
          registered marks + common-law web sweep
        </span>
      </div>
      <div className="flex gap-2">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          spellCheck={false}
          className="flex-1 rounded-lg border border-stone-800 bg-stone-950/60 px-3 py-2 font-display text-xl tracking-wider text-stone-100 outline-none focus:border-amber-500/50"
        />
        <button
          onClick={run}
          disabled={running || !title.trim()}
          className="rounded-lg border border-amber-400/60 px-4 font-display text-xl tracking-wider text-amber-400 transition hover:bg-amber-400/10 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {running ? 'SWEEPING…' : 'CHECK TITLE'}
        </button>
      </div>

      {stage && (
        <p className="pulse-soft mt-3 font-mono text-[10px] uppercase tracking-widest text-sky-400">
          🔍 {STAGE_LABEL[stage] ?? stage}
        </p>
      )}
      {error && (
        <p className="mt-3 rounded border border-red-500/40 bg-red-950/40 p-2 font-mono text-xs text-red-300">
          {error}
        </p>
      )}

      {verdict && (
        <div className="card-in mt-4 border-t border-stone-800 pt-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <span className="font-display text-2xl tracking-wide text-stone-100">
                {verdict.title}
              </span>
              <span className="ml-3 font-mono text-[10px] text-stone-500">
                confusion risk {verdict.risk_score}
              </span>
            </div>
            <span
              className={`stamp-in shrink-0 rounded border-2 px-2 py-0.5 font-display text-lg tracking-widest ${STAMP[verdict.verdict]}`}
            >
              {verdict.verdict}
            </span>
          </div>
          <p className="mt-2 text-[12px] leading-snug text-stone-300">{verdict.rationale}</p>
          <p className="mt-1 text-[12px] leading-snug text-stone-300">
            <span className="font-mono text-[9px] uppercase tracking-widest text-amber-400/80">
              fix ·{' '}
            </span>
            {verdict.recommendation}
          </p>

          {verdict.conflicts.length > 0 && (
            <ul className="mt-3 space-y-1">
              {verdict.conflicts.map((c) => (
                <li key={`${c.name}-${c.url}`} className="flex items-baseline gap-2 text-[12px]">
                  <span
                    className={`font-mono text-[9px] uppercase ${
                      c.severity === 'HIGH'
                        ? 'text-red-400'
                        : c.severity === 'MEDIUM'
                          ? 'text-amber-400'
                          : 'text-stone-500'
                    }`}
                  >
                    {c.severity}
                  </span>
                  <span className="text-stone-200">{c.name}</span>
                  <span className="text-stone-500">
                    {c.medium} · {c.year}
                  </span>
                  <a
                    href={c.url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-[9px] text-sky-500 underline decoration-dotted hover:text-sky-300"
                  >
                    source
                  </a>
                </li>
              ))}
            </ul>
          )}

          {alternates.length > 0 && (
            <div className="mt-4">
              <p className="mb-2 font-mono text-[10px] uppercase tracking-widest text-stone-400">
                cleared alternates — each screened live
              </p>
              <div className="flex flex-wrap gap-2">
                {alternates.map((a) => (
                  <span
                    key={a.title}
                    title={a.note}
                    className="card-in flex items-center gap-2 rounded border border-stone-700 bg-stone-950/60 px-3 py-1.5"
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${DOT[a.verdict]}`} />
                    <span className="font-display text-lg tracking-wide text-stone-100">
                      {a.title}
                    </span>
                    <span className="font-mono text-[9px] uppercase text-stone-500">
                      {a.verdict}
                    </span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
```

### `web/src/components/EoBinder.tsx`

The underwriter checklist and the AI-usage insurability intake.

```tsx
import { useMemo, useState } from 'react'
import type { AiCheckEvent, AiResult, EoRow, TitleVerdict, Verdict } from '../types'
import { streamSSE } from '../lib/stream'
import { safeHostname } from '../lib/url'

const AI_USAGES: { id: string; label: string }[] = [
  { id: 'ai_voice', label: 'AI voice / narration' },
  { id: 'ai_imagery', label: 'AI imagery / VFX' },
  { id: 'ai_music', label: 'AI music elements' },
  { id: 'ai_writing', label: 'AI-assisted writing' },
  { id: 'ai_likeness', label: 'Digital replica of a real person' },
]

const STATUS_STYLE: Record<string, { chip: string; label: string }> = {
  clear: { chip: 'border-emerald-500/50 text-emerald-400', label: 'CLEAR' },
  flagged: { chip: 'border-red-500/50 text-red-400', label: 'ACTION REQUIRED' },
  pending: { chip: 'border-stone-600 text-stone-400', label: 'PENDING' },
  out_of_scope: { chip: 'border-stone-700 text-stone-500', label: 'DOCUMENTS REQUIRED' },
}

const VERDICT_TEXT: Record<Verdict, string> = {
  CLEAR: 'text-emerald-400',
  CAUTION: 'text-amber-400',
  BLOCKED: 'text-red-400',
}

function worstVerdict(results: { verdict: Verdict }[]): Verdict | undefined {
  const order: Verdict[] = ['BLOCKED', 'CAUTION', 'CLEAR']
  return order.find((v) => results.some((r) => r.verdict === v))
}

export function EoBinder({
  rows,
  titleVerdict,
}: {
  rows: EoRow[]
  titleVerdict: TitleVerdict | null
}) {
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [aiResults, setAiResults] = useState<AiResult[]>([])
  const [aiRunning, setAiRunning] = useState(false)
  const [aiError, setAiError] = useState<string | null>(null)

  const effectiveRows = useMemo(() => {
    return rows.map((row) => {
      if (row.id === 'title_report' && titleVerdict) {
        const flagged = titleVerdict.verdict !== 'CLEAR'
        return {
          ...row,
          status: (flagged ? 'flagged' : 'clear') as EoRow['status'],
          worst: flagged ? titleVerdict.verdict : undefined,
          note: `TitleGuard: "${titleVerdict.title}" — ${titleVerdict.verdict}, confusion risk ${titleVerdict.risk_score}.`,
          findings: [],
        }
      }
      if (row.id === 'ai_usage_disclosure' && aiResults.length > 0) {
        const worst = worstVerdict(aiResults)
        const flagged = worst !== 'CLEAR'
        return {
          ...row,
          status: (flagged ? 'flagged' : 'clear') as EoRow['status'],
          worst: flagged ? worst : undefined,
          note: `${aiResults.length} AI usage${aiResults.length > 1 ? 's' : ''} researched against current carrier language.`,
          findings: [],
        }
      }
      return row
    })
  }, [rows, titleVerdict, aiResults])

  const covered = effectiveRows.filter((r) => r.status === 'clear').length
  const actionable = effectiveRows.filter((r) => r.status !== 'out_of_scope').length

  const runAiCheck = async () => {
    setAiRunning(true)
    setAiError(null)
    setAiResults([])
    try {
      await streamSSE<AiCheckEvent>('/api/eo/ai-check', { usages: [...checked] }, (ev) => {
        if (ev.type === 'ai_result') {
          const { type: _t, ...r } = ev
          setAiResults((prev) => [...prev, r as AiResult])
        } else if (ev.type === 'error') {
          setAiError(ev.message)
        }
      })
    } catch (e) {
      setAiError(String(e))
    } finally {
      setAiRunning(false)
    }
  }

  return (
    <div className="mt-6">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-display text-2xl tracking-wide text-stone-100">
          E&amp;O BINDER <span className="text-stone-500">· underwriter checklist</span>
        </h3>
        <span className="font-mono text-[10px] uppercase tracking-widest text-stone-400">
          {covered}/{actionable} procedures clear · no E&amp;O, no distribution{' '}
          <a
            href="https://www.frontrowinsurance.com/errors-omissions-insurance-101"
            target="_blank"
            rel="noreferrer"
            className="text-sky-500 underline decoration-dotted hover:text-sky-300"
          >
            source
          </a>
        </span>
      </div>

      <ol className="divide-y divide-stone-800 rounded-lg border border-stone-800 bg-stone-950/50">
        {effectiveRows.map((row, i) => {
          const style = STATUS_STYLE[row.status]
          return (
            <li key={row.id} className="flex items-start gap-3 px-4 py-2.5">
              <span className="mt-0.5 w-5 shrink-0 font-mono text-[10px] text-stone-600">
                {String(i + 1).padStart(2, '0')}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-[13px] font-medium text-stone-100">{row.title}</span>
                  <a
                    href={row.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-[9px] text-sky-600 underline decoration-dotted hover:text-sky-400"
                  >
                    req
                  </a>
                </div>
                <p className="text-[11px] leading-snug text-stone-400">{row.note}</p>
                {row.findings.length > 0 && (
                  <p className="mt-0.5 text-[11px] leading-snug">
                    {row.findings.slice(0, 4).map((f, j) => (
                      <span key={f.id}>
                        {j > 0 && <span className="text-stone-600"> · </span>}
                        <span className="text-stone-300">{f.name}</span>{' '}
                        <span className={`font-mono text-[9px] ${VERDICT_TEXT[f.verdict]}`}>
                          {f.verdict}
                        </span>
                      </span>
                    ))}
                    {row.findings.length > 4 && (
                      <span className="text-stone-600"> +{row.findings.length - 4} more</span>
                    )}
                  </p>
                )}
              </div>
              <span
                className={`mt-0.5 shrink-0 rounded border px-2 py-0.5 font-mono text-[9px] uppercase tracking-widest ${style.chip}`}
              >
                {row.status === 'flagged' && row.worst ? row.worst : style.label}
              </span>
            </li>
          )
        })}
      </ol>

      {/* AI-usage intake — the results print, only the controls are screen-only */}
      <div className="mt-4 rounded-lg border border-stone-800 bg-stone-950/50 p-4">
        <div className="mb-2 flex items-center justify-between">
          <h4 className="font-mono text-[10px] uppercase tracking-[0.2em] text-stone-400">
            🤖 ai-usage intake — carrier exclusion language researched live via parallel
          </h4>
        </div>
        <div className="no-print flex flex-wrap items-center gap-2">
          {AI_USAGES.map((u) => (
            <label
              key={u.id}
              className={`flex cursor-pointer items-center gap-1.5 rounded border px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-wide transition focus-within:ring-2 focus-within:ring-amber-400 ${
                checked.has(u.id)
                  ? 'border-amber-400/60 bg-amber-400/10 text-amber-300'
                  : 'border-stone-700 text-stone-400 hover:border-stone-500'
              }`}
            >
              <input
                type="checkbox"
                className="sr-only"
                checked={checked.has(u.id)}
                onChange={() =>
                  setChecked((prev) => {
                    const next = new Set(prev)
                    if (next.has(u.id)) next.delete(u.id)
                    else next.add(u.id)
                    return next
                  })
                }
              />
              {u.label}
            </label>
          ))}
          <button
            onClick={runAiCheck}
            disabled={aiRunning || checked.size === 0}
            className="ml-auto rounded border border-amber-400/60 px-3 py-1.5 font-display text-lg tracking-wider text-amber-400 transition hover:bg-amber-400/10 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {aiRunning ? 'RESEARCHING CARRIERS · PARALLEL' : 'CHECK INSURABILITY'}
          </button>
        </div>
        {aiError && (
          <p className="mt-3 rounded border border-red-500/40 bg-red-950/40 p-2 font-mono text-xs text-red-300">
            {aiError}
          </p>
        )}
        {aiResults.length > 0 && (
          <ul className="mt-3 space-y-2">
            {aiResults.map((r) => (
              <li key={r.usage} className="card-in flex items-start gap-3 text-[12px]">
                <span
                  className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase ${
                    r.verdict === 'CLEAR'
                      ? 'border-emerald-500/50 text-emerald-400'
                      : r.verdict === 'CAUTION'
                        ? 'border-amber-400/50 text-amber-400'
                        : 'border-red-500/50 text-red-400'
                  }`}
                >
                  {r.verdict}
                </span>
                <span className="min-w-0 leading-snug text-stone-300">
                  <span className="text-stone-100">{r.label}.</span> {r.guidance}{' '}
                  {r.sources.slice(0, 2).map((s) => (
                    <a
                      key={s.url}
                      href={s.url}
                      target="_blank"
                      rel="noreferrer"
                      className="mr-1 font-mono text-[9px] text-sky-500 underline decoration-dotted hover:text-sky-300"
                    >
                      {safeHostname(s.url)}
                    </a>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
```

### `web/src/components/ReportPanel.tsx`

The triage report: verdict tiles, the time/cost benchmark, and PDF export.

```tsx
import type { Report, TitleVerdict } from '../types'
import { EoBinder } from './EoBinder'

const TILE_STYLES: Record<string, string> = {
  CLEAR: 'border-emerald-500/40 text-emerald-400',
  CAUTION: 'border-amber-400/40 text-amber-400',
  BLOCKED: 'border-red-500/40 text-red-500',
}

export function ReportPanel({
  report,
  title,
  heading = 'FINAL CLEARANCE REPORT',
  titleVerdict = null,
}: {
  report: Report
  title: string
  heading?: string
  titleVerdict?: TitleVerdict | null
}) {
  return (
    <section
      id="clearance-report"
      className="card-in mt-10 rounded-xl border border-amber-400/30 bg-stone-900/70 p-6"
    >
      <div className="mb-4 flex items-center justify-between">
        <h2 className="font-display text-3xl tracking-wide text-amber-400">{heading}</h2>
        <span className="flex items-center gap-3 font-mono text-[10px] uppercase tracking-widest text-stone-400">
          {title}
          <button
            onClick={() => window.print()}
            className="no-print rounded border border-stone-600 px-2 py-1 uppercase tracking-widest text-stone-300 transition hover:border-amber-400/60 hover:text-amber-400"
          >
            ⬇ export pdf
          </button>
        </span>
      </div>

      <div className="mb-5 grid grid-cols-3 gap-3">
        {(['CLEAR', 'CAUTION', 'BLOCKED'] as const).map((v) => (
          <div
            key={v}
            className={`rounded-lg border bg-stone-950/60 p-4 text-center ${TILE_STYLES[v]}`}
          >
            <div className="font-display text-4xl">{report.stats[v] ?? 0}</div>
            <div className="font-mono text-[10px] uppercase tracking-[0.25em]">{v}</div>
          </div>
        ))}
      </div>

      {report.elapsed_seconds != null && (
        <div className="mb-5 flex flex-wrap items-baseline gap-x-6 gap-y-1 rounded-lg border border-stone-800 bg-stone-950/60 px-4 py-3">
          <span className="font-mono text-[10px] uppercase tracking-widest text-stone-400">
            human clearance report:{' '}
            <span className="text-stone-300">$1,000–$3,000 · 5–10 business days</span>{' '}
            <a
              href="https://www.coastalclearances.com/script-clearance-reports"
              target="_blank"
              rel="noreferrer"
              className="text-sky-500 underline decoration-dotted hover:text-sky-300"
            >
              source
            </a>
          </span>
          <span className="font-mono text-[10px] uppercase tracking-widest text-amber-400">
            this report: {Math.round(report.elapsed_seconds)}s · {report.searches} live searches ·{' '}
            {report.sources} sources
          </span>
        </div>
      )}
      <p className="max-w-4xl text-[14px] leading-relaxed text-stone-200">{report.summary}</p>

      {report.eo_checklist && <EoBinder rows={report.eo_checklist} titleVerdict={titleVerdict} />}

      <p className="mt-4 font-mono text-[10px] uppercase tracking-widest text-stone-400">
        breakdown: gemini via google adk · evidence: parallel search api · assessment: gemini
        structured output · counsel review still required before principal photography
      </p>
    </section>
  )
}
```

### `web/src/components/ErrorBoundary.tsx`

Last-resort boundary so a render fault never shows a blank page.

```tsx
import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * A crash while drawing one card must not blank the whole board mid-demo.
 * A malformed source URL or an unexpected event shape throws in render; without
 * a boundary that unmounts the entire app and the run you just watched is gone.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('ClearanceRoom render error:', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="min-h-screen bg-stone-950 p-6">
        <div className="mx-auto max-w-3xl rounded-lg border border-red-500/40 bg-red-950/30 p-6">
          <h1 className="font-display text-3xl tracking-wide text-red-400">RENDER FAULT</h1>
          <p className="mt-2 text-sm text-stone-300">
            The interface hit an error while drawing the board. Reload to start a fresh run.
          </p>
          <pre className="mt-4 overflow-x-auto rounded border border-stone-800 bg-stone-950 p-3 font-mono text-[11px] text-red-300">
            {error.message}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            className="mt-4 rounded border border-stone-700 px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-stone-300 hover:border-amber-500/60 hover:text-amber-400"
          >
            try rendering again
          </button>
        </div>
      </div>
    )
  }
}
```

### `web/src/main.tsx`

React entry point — mounts the app inside the error boundary.

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
```

### `web/src/index.css`

Theme, the film-grain overlay, the stamp/card animations, and the print stylesheet that turns the report into a clearance-firm deliverable.

```css
@import 'tailwindcss';

@theme {
  --font-display: 'Bebas Neue', sans-serif;
  --font-sans: 'Inter', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
}

body {
  background: #0c0a09;
  color: #e7e5e4;
  font-family: var(--font-sans);
}

/* subtle film grain */
.grain::before {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  opacity: 0.05;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  z-index: 50;
}

@keyframes pulse-soft {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}
.pulse-soft { animation: pulse-soft 1.4s ease-in-out infinite; }

@keyframes stamp-in {
  0% { transform: scale(2.2) rotate(-14deg); opacity: 0; }
  60% { transform: scale(0.92) rotate(-8deg); opacity: 1; }
  100% { transform: scale(1) rotate(-8deg); opacity: 1; }
}
.stamp-in { animation: stamp-in 0.35s cubic-bezier(0.2, 1.4, 0.4, 1) both; }

@keyframes card-in {
  from { transform: translateY(10px); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}
.card-in { animation: card-in 0.3s ease-out both; }

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.15; }
}
.blink { animation: blink 1.1s step-end infinite; }

/* Print: the report as a clearance-firm deliverable — light, ink-friendly */
@media print {
  body { background: #fff; color: #1c1917; }
  .grain::before { display: none; }
  header, footer, .no-print { display: none !important; }
  main > .mb-6, /* mode switch */
  main .grid.lg\:grid-cols-\[minmax\(320px\,2fr\)_3fr\] > section:first-child { display: none; }
  /* Collapse only the page's top-level two-column layout — NOT the report's own
     stat grid, which must keep its columns in print. */
  main > .grid { grid-template-columns: 1fr !important; }
  #clearance-report {
    margin-top: 0;
    border-color: #d6d3d1;
    background: #fff !important;
  }
  #clearance-report *, article, .bg-stone-900\/60, .bg-stone-950\/50, .bg-stone-950\/60 {
    background: #fff !important;
    border-color: #d6d3d1 !important;
  }
  .text-stone-100, .text-stone-200, .text-stone-300 { color: #1c1917 !important; }
  .text-stone-400, .text-stone-500, .text-stone-600 { color: #57534e !important; }
  .text-amber-400, .text-amber-400\/80, .text-amber-300 { color: #92400e !important; }
  .bg-stone-900\/40, .bg-amber-950\/30, .bg-amber-400\/10, .bg-red-950\/20 {
    background: #fff !important;
  }
  .text-emerald-400 { color: #065f46 !important; }
  .text-red-400, .text-red-500 { color: #991b1b !important; }
  .text-sky-400, .text-sky-500 { color: #075985 !important; }
  article { break-inside: avoid; }
  #clearance-report li { break-inside: avoid; }
}
```

### `web/index.html`

Document shell, fonts, and the social/meta tags.

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>ClearanceRoom — every frame cleared</title>
    <meta
      name="description"
      content="AI script clearance in minutes, not weeks. Gemini breaks your screenplay into clearable items, Parallel Search researches each on the live web, and you get an E&O-ready clearance report."
    />
    <meta property="og:title" content="ClearanceRoom — every frame cleared" />
    <meta
      property="og:description"
      content="AI script clearance in minutes, not weeks. A deterministic Gemini + Parallel Search agent that researches every brand, song, artwork, and claim in your screenplay."
    />
    <meta property="og:type" content="website" />
    <meta property="og:url" content="https://clearanceroom-957638696965.us-central1.run.app" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:ital,wght@0,400;0,500;0,600;1,400&family=JetBrains+Mono:wght@400;600&display=swap"
      rel="stylesheet"
    />
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🎬</text></svg>" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

### `web/vite.config.ts`

Vite config and the dev proxy to the API.

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://localhost:8801',
    },
  },
})
```


## Infrastructure

### `Dockerfile`

Multi-stage build: Node builds the UI, Python serves the pipeline and the built static files.

```dockerfile
# ---- frontend build ----
FROM node:22-slim AS webbuild
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- runtime ----
FROM python:3.13-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY scripts ./scripts
COPY --from=webbuild /web/dist ./static
ENV STATIC_DIR=/app/static
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
```

### `backend/requirements.txt`

```text
google-genai>=2.17.0
google-adk>=2.6.0
fastapi>=0.115
uvicorn[standard]>=0.30
httpx>=0.27
python-dotenv>=1.0
pydantic>=2.8
parallel-web>=1.3.0
pytest>=8.0
```

### `backend/.env.example`

Configuration template. The real `.env` is gitignored and has never been committed.

```
# ClearanceRoom backend configuration — copy to .env

# 1 = run full pipeline with canned data (no keys needed). 0 = live.
MOCK_MODE=1

# --- Google Cloud (live mode) ---
# Requires: gcloud auth application-default login
GOOGLE_CLOUD_PROJECT=
GOOGLE_CLOUD_LOCATION=global
GEMINI_MODEL=gemini-3.6-flash
GEMINI_REPORT_MODEL=gemini-3.1-pro-preview

# --- Parallel Search API (live mode) ---
# Get a key at https://platform.parallel.ai
PARALLEL_API_KEY=
PARALLEL_MODE=advanced

RESEARCH_CONCURRENCY=4

# Deep Dossier (Parallel Task API). "-fast" variants: 2-5x quicker, same price.
PARALLEL_TASK_PROCESSOR=core-fast
```


## Sample screenplays

Original scripts written as test fixtures, each deliberately salted with clearance landmines.

### `scripts/midnight_static.txt`

A short noir carrying a sung Beatles song, a Casablanca clip on a diner TV, a Banksy mural as a story beat, famous-brand wardrobe, and a fictional diner name that turned out to be a real business.

```text
MIDNIGHT STATIC

Written by
M. Williams

FADE IN:

EXT. THE BLUE COMET DINER - NIGHT

Rain hammers a dying neon sign. A massive COCA-COLA billboard
glows red across the street, reflected in every puddle.

A Ford Crown Victoria idles at the curb, wipers losing the
fight.

INT. THE BLUE COMET DINER - CONTINUOUS

Mostly empty. A TV above the counter plays the airport scene
from CASABLANCA, volume low.

DANNY VOSS, 40s, rain-soaked in a beat-up NIKE windbreaker,
slides into a corner booth. He checks a Rolex Submariner that
looks two owners past legitimate.

The WAITRESS, 60s, pours coffee without asking.

                    WAITRESS
          You look like a man who lost an
          argument with the weather.

                    DANNY
          The weather cheated.

He glances at the wall. A faded ELVIS PRESLEY poster hangs
crooked next to the pie case.

                    WAITRESS
          You waiting on somebody?

                    DANNY
          My niece. Sixteen. Thinks she's
          gonna be bigger than Taylor Swift.
          I told her Taylor Swift also
          thought that, and look what
          happened.

The waitress snorts, moves off. Danny pulls out a burner
phone, dials.

                    DANNY (CONT'D)
              (into phone)
          It's me. Call me back at
          555-0147. Don't use the other
          number.

He hangs up. Beat. Then, softly, almost to himself, he starts
to SING.

                    DANNY (CONT'D)
              (singing)
          Hey Jude... don't make it bad...

                    WAITRESS (O.S.)
          No singing in the booths. House
          rule.

EXT. ALLEY BEHIND THE DINER - LATER

Danny smokes under a fire escape. The alley wall carries a
BANKSY mural — the girl with the red balloon — tagged over
twice but unmistakable.

His phone BUZZES. He answers.

                    DANNY
          Yeah.

                    VOICE (V.O.)
              (filtered)
          Chrysler Building. Observation
          level. Tomorrow, nine sharp.
          Bring the drive.

                    DANNY
          The Chrysler Building doesn't
          have an observation level. It
          closed in 1945.

                    VOICE (V.O.)
          Then you'd better find a way up.

CLICK. Danny stares at the phone. Behind him, the Banksy girl
reaches for her balloon, forever.

                                                    CUT TO:

INT. THE BLUE COMET DINER - CONTINUOUS

On the TV, Bogart tells Bergman they'll always have Paris.

The waitress watches it, towel over her shoulder.

                    WAITRESS
          They don't make 'em like that
          anymore.

FADE OUT.

THE END
```

### `scripts/static_and_lightning.txt`

A 'based on a true story' Tesla biopic with six planted falsehoods — a fake Nobel Prize, an arson claim, and a wife who never existed.

```text
STATIC & LIGHTNING

"Based on a true story"

Written by
M. Williams

FADE IN:

TITLE CARD: NEW YORK CITY, 1943.

INT. HOTEL NEW YORKER - ROOM 3327 - NIGHT

Sparse. A desk of ordered papers. Pigeons roost on the sill.
NIKOLA TESLA, 86, gaunt in a threadbare suit, feeds them
crumbs.

                    NARRATOR (V.O.)
          He died here. Penniless. Room
          3327 — a number divisible by
          three, the way he liked it.

INT. EDISON MACHINE WORKS - 1885 - FLASHBACK

Young TESLA works feverishly at a dynamo. THOMAS EDISON
watches from the doorway.

                    NARRATOR (V.O.)
          He had come to America to work
          for Edison. He quit when the
          great man refused to pay the
          fifty-thousand-dollar bonus he
          had promised.

                    EDISON
          Tesla, you don't understand our
          American humor.

INT. GRAND BALLROOM - 1915 - FLASHBACK

Tesla, mid-50s, holds court at a banquet table. A REPORTER
leans in.

                    REPORTER
          Congratulations on the Nobel
          Prize, Mr. Tesla. Shared with
          Edison, no less.

                    TESLA
          I would refuse to share anything
          with a man who electrocutes
          elephants for the newspapers.

                    NARRATOR (V.O.)
          The Nobel was his. He never
          forgave the committee for making
          him share it.

EXT. FIFTH AVENUE LABORATORY - 1895 - NIGHT - FLASHBACK

Flames pour from the windows. Tesla watches from the street,
lit orange.

                    NARRATOR (V.O.)
          When the money ran out, he
          burned the laboratory himself
          for the insurance. The papers
          never knew.

INT. WARDENCLYFFE TOWER - 1904 - FLASHBACK

Tesla stands beneath the great unfinished dome. A LETTER in
his hand. J.P. MORGAN's seal.

                    NARRATOR (V.O.)
          Morgan cut the funding with one
          line: "If anyone can draw on the
          power, where do we put the
          meter?"

INT. HOTEL NEW YORKER - ROOM 3327 - NIGHT - PRESENT

Tesla sets down the crumbs. Looks at a photograph of a woman
in an oval frame.

                    NARRATOR (V.O.)
          His wife had left him decades
          ago. He kept her picture anyway.
          Marconi took the patents. The
          Supreme Court gave them back
          five months after his heart gave
          out.

He closes his eyes. Outside, the city hums with alternating
current.

FADE OUT.

THE END
```


## Deploy

Cloud Run's `--timeout` covers the **total** duration of a streaming
response, not just idle time — the 300s default kills a long run mid-stream,
and SSE keepalives do not extend it.

```bash
gcloud run deploy clearanceroom \
  --source . --project <PROJECT_ID> --region us-central1 \
  --allow-unauthenticated --memory 1Gi \
  --timeout=3600 --max-instances=2 \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=<PROJECT_ID>,GOOGLE_CLOUD_LOCATION=global,GEMINI_MODEL=gemini-3.6-flash,GEMINI_REPORT_MODEL=gemini-3.1-pro-preview,PARALLEL_API_KEY=<KEY>,PARALLEL_MODE=advanced,PARALLEL_TASK_PROCESSOR=core-fast"
```

## Run locally

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # MOCK_MODE=1 works with no keys at all
.venv/bin/uvicorn app.main:app --port 8801
```
```bash
cd web && npm install && npm run dev -- --port 5177   # proxies /api to :8801
```
```bash
cd backend && MOCK_MODE=1 .venv/bin/python -m pytest tests/ -q
```
