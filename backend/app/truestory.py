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
from .pipeline import PRECEDENTS, VERDICTS, _adk_agent, _run_adk

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
            raw = await _run_adk(agent, script_text)
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
                    result = await _assess_claim(claim, evidence)
                    record = {**claim, **result, "sources": [
                        {"url": ev["url"], "title": ev["title"]} for ev in evidence
                    ]}
                    if result["verdict"] != "CLEAR":
                        record["precedent"] = PRECEDENTS["PERSON"]
                    assessed.append(record)
                    await emit({"type": "entity_result", "id": claim["id"],
                                **result, "sources": record["sources"],
                                "precedent": record.get("precedent")})

            await asyncio.gather(*(investigate(c) for c in claims))
            await emit({"type": "stage", "stage": "research", "status": "done"})
            await emit({"type": "stage", "stage": "assess", "status": "done"})

            # -- Stage 4: REPORT -----------------------------------------
            await emit({"type": "stage", "stage": "report", "status": "start"})
            order = {"BLOCKED": 0, "CAUTION": 1, "CLEAR": 2}
            assessed.sort(key=lambda a: (order.get(a["verdict"], 3), -a["risk_score"]))
            report_agent = _adk_agent("defamation_report", TS_REPORT_INSTRUCTION,
                                      None, config.GEMINI_REPORT_MODEL)
            summary = await _run_adk(report_agent, json.dumps(assessed, indent=1))
            stats = {v: sum(1 for a in assessed if a["verdict"] == v) for v in VERDICTS}
            await emit({"type": "report", "summary": summary.strip(),
                        "stats": stats, "items": assessed,
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
