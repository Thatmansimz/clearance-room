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
