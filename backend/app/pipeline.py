"""ClearanceRoom pipeline — a multi-step clearance agent.

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
        # Clearance is a legal workflow: given the same evidence, the same script
        # must grade the same way twice. (The evidence itself is live web research,
        # so it moves as the web moves — that is the point of researching.)
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


def clamp_ungrounded_verdict(result: dict[str, Any],
                            evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """A green light must be earned by evidence.

    With no sources the verdict is model prior only, so cap it at CAUTION — the
    tool is tuned to over-flag rather than under-flag, and this is the branch
    that safety property actually rests on.
    """
    if not evidence and result["verdict"] == "CLEAR":
        result["verdict"] = "CAUTION"
        result["rationale"] = (
            "No web evidence was retrieved for this item, so it cannot be cleared "
            "on the record. " + result["rationale"]
        )
    return result


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
    return clamp_ungrounded_verdict(result, evidence)


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
