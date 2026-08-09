# Handoff — state as of 2026-08-09

## Where things stand

**Live and verified.** Prod: https://clearanceroom-957638696965.us-central1.run.app
(revision 00005, `--timeout=3600`, `--max-instances=2`, both engines live).
Repo: https://github.com/Thatmansimz/clearance-room (public, MIT, clean tree).
Last verified prod run: 72s, 15 items, 5 CLEAR / 9 CAUTION / 1 BLOCKED,
zero CLEAR-without-evidence violations.

Submission checklist: hosted URL ✅ · public repo + license ✅ · Google Cloud
and Parallel at runtime ✅ · **demo video ❌** · **Devpost form ❌**.
Deadline **Sept 7 2026, 2:00pm PDT**. Track: **Parallel**.

## What shipped

Four capabilities: Script Clearance, True-Story Shield (defamation fact-check),
TitleGuard (title collision sweep), E&O Binder (12 underwriter procedures +
live AI-usage insurability intake). 15 pytest cases pass (`MOCK_MODE=1
.venv/bin/python -m pytest tests/ -q`).

## Key operational facts

- GCP project `clearanceroom-2026`, personal account (mwilliamsii820@gmail.com),
  personal billing. Deliberately separate from Tridorian.
- Models `gemini-3.6-flash` + `gemini-3.1-pro-preview`, **location must be
  `global`** — Gemini 3.x 404s in us-central1.
- Parallel key lives in gitignored `backend/.env`; also set as a Cloud Run env var.
- Local: `clearance-api` on 8801, `clearance-web` on 5177 (see ~/.claude/launch.json).
- Deploy command with all required flags: [`docs/DEPLOY.md`](DEPLOY.md).

## Parallel, used three ways (the "Tournament of Champions" plate)

The partner platform is much larger than the Search API. Verified available on
this account: Search, Extract, Task (+ Task Groups), Responses, Chat, Monitor,
FindAll, Entity Search, and two MCP servers.

1. **SCREEN — Search API** *(shipped)*. Breadth: one objective per entity,
   whole script graded in ~60s. Next upgrade: `advanced_settings.source_policy.
   include_domains = ["uspto.gov", ".gov", "ascap.com", "bmi.com"]` to pin
   evidence to authoritative registries (~1 hour, and the real fix for the
   Casablanca-class relevance problem).
2. **ADJUDICATE — Task API** *(shipped, `backend/app/dossier.py`)*. Depth on
   demand: structured output where every field carries citations + reasoning +
   **confidence**. Processor `core-fast` — measured 28s vs 150s for `core` at
   identical pricing. Verified in prod at 37s.
3. **CURE — Extract API** *(not built)*. Close the loop: Gemini proposes a
   substitution, Extract pulls the authoritative record verbatim to prove the
   substitute is itself clear. This is the "FIX IT" button the red-team said the
   winning entry would have. ~a day.
4. *(optional 4th)* **Monitor API** — snapshot monitors over completed Task runs
   for clearance decay ("your cleared song's catalog was just sold").

**Demo gold, unbuilt:** Task API SSE events (`GET /v1/tasks/runs/{id}/events`,
`enable_events: true`) expose the agent's OWN search queries, prefixed
`Objective:`. Streaming those into the UI shows a research plan executing live —
the single strongest answer to "where's the agent?".

## Open decisions for Michael (do not action without his call)

- **`docs/AUDIT.md` is public** and reads as a 79-finding prosecution of our own
  product. Recommendation: rewrite as a fixed/open changelog rather than delete —
  the rigor is a selling point, the hit-list framing is not.
- **Commit trailers say `Co-Authored-By: Claude`.** The project itself uses only
  Google AI (compliant), but the trailers are visible in public history on a
  Google-sponsored entry. Rewriting published history is destructive — his call.

## Next actions, in priority order

1. **Record the 3-minute demo video.** Full shot list, VO script, run-sheet, and
   end card are ready in [`docs/TRAILER.md`](TRAILER.md) and `docs/endcard.html`.
   This is the last hard blocker on submission.
2. **Fill the Devpost form**, then add the video + Devpost links to the README
   (a cold-visiting judge currently finds neither).
3. **Add a README screenshot** — the war-room UI is the strongest asset and is
   invisible to anyone who only reads the repo. A mermaid architecture diagram
   is already in the README; a screenshot is not.
4. Work the punch list in [`docs/AUDIT.md`](AUDIT.md) — 79 findings from seven
   hostile lenses. All 6 blockers and most majors are fixed; the highest-value
   remaining items are:
   - **Parallel Task API** for one lane (deep research with per-field citations
     and confidence). The red-team's verdict: the entry that beats us uses both
     Parallel APIs while we use only Search. Highest-ROI remaining feature.
   - **One adaptive hop**: when a search returns nothing, have Gemini reformulate
     and re-search once, emitting a visible "agent retried" event. This is the
     direct answer to "where's the agent?" in an *Agentic* Cinema hackathon.
   - **Recall table**: MIDNIGHT STATIC is salted with known landmines — publish
     caught/planted per category across 5 runs to answer "what's your
     false-negative rate?"
   - Parallel 429/5xx retry; `_sse` pump swallows generator exceptions.

## Prepared answers for judges

- *"Is it really deterministic?"* — Control flow is fixed in code and every model
  call runs at `temperature=0`. Same script, same grades.
- *"Where's the agent?"* — Agency is inside the stages: Gemini decides what
  counts as clearable, each entity gets its own research objective, verdicts are
  graded against retrieved evidence. Fixed control flow is a *feature* for a
  legal workflow — a clearance report that changes between runs is worthless.
- *"Why Parallel over Gemini's Search grounding?"* — Clearance needs an auditable
  evidence trail we control: per-entity objectives, and every verdict traceable
  to the sources shown on the card.
