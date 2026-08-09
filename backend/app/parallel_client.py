"""Parallel Search API client — the research engine of ClearanceRoom.

Runtime partner integration for the Parallel track: every entity extracted from
a script is researched through Parallel's Search API (POST /v1/search) to gather
live web evidence on trademark status, rights holders, and prior disputes.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from . import config, mockdata


class ParallelSearchError(RuntimeError):
    pass


async def search(objective: str, search_queries: list[str]) -> dict[str, Any]:
    """Run one Parallel Search call. Returns the raw API response dict."""
    if config.MOCK_PARALLEL:
        return await mockdata.mock_search(objective)

    if not config.PARALLEL_API_KEY:
        raise ParallelSearchError("PARALLEL_API_KEY is not set (or enable MOCK_MODE=1)")

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            config.PARALLEL_API_URL,
            headers={
                "x-api-key": config.PARALLEL_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "objective": objective,
                "search_queries": search_queries,
                "mode": config.PARALLEL_MODE,
            },
        )
        if resp.status_code != 200:
            raise ParallelSearchError(f"Parallel Search {resp.status_code}: {resp.text[:300]}")
        return resp.json()


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
