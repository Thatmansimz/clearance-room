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
