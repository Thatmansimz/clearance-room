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
    usages = [(u, AI_USAGES[u]) for u in usage_ids if u in AI_USAGES]
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
