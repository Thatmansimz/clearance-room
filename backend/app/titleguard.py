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
