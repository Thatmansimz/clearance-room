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
