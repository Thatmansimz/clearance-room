# Hardening log

ClearanceRoom is a legal-workflow tool, so we test it the way one gets tested:
adversarially. We ran seven independent review passes over the deployed product
— simulated judging, backend, frontend and accessibility, user-facing copy, a
cold repository visit, live output quality read by a clearance-counsel lens, and
a competitive red-team — and worked the results.

This is the log. It is here because the failures are as informative as the
features: several of these would have taken the product down during a live run.

## Fixed

**Streaming survives a full-length run.** Cloud Run's request timeout covers the
*total* duration of a streaming response, not just idle time, so SSE keepalive
frames do not extend it. A long clearance run against the 300s default would have
died mid-stream. The service now deploys with `--timeout=3600`, and the
misleading comment that started the problem is gone. → `docs/DEPLOY.md`

**"Deterministic" is now true of the outputs, not just the stage order.** Every
Gemini call runs at `temperature=0`. Before this, two runs of the same script
could return different entity counts and different verdicts while the README
claimed determinism. A clearance report that changes between runs is worthless.
→ `backend/app/pipeline.py`

**A finding can never be cleared without evidence.** If web research returns
nothing, the verdict is capped at `CAUTION` in code and the rationale says so.
The tool over-flags by design; a green light has to be earned by a source.
→ `backend/app/pipeline.py`

**Research is grounded in the right sources.** Queries carry the category context
the breakdown stage derived. Previously a bare entity name went to search, and
the top BLOCKED item on our sample script — a *Casablanca* clip — came back
"supported" by trademark records for unrelated ceiling-fan and polo-shirt marks.
The verdict was right by luck. It now returns Warner Bros. clip licensing and
Swank Motion Pictures, the film's actual licensing distributor.
→ `backend/app/parallel_client.py`

**Per-item failure isolation everywhere.** True-Story Shield had none: one bad
model response aborted an entire run after every other claim had already been
researched and paid for. It now degrades a single claim to an unreviewed
`CAUTION` and finishes. Stages 1 and 4 retry once, and a failed executive
summary falls back to a deterministic one rather than discarding a completed run.
→ `backend/app/truestory.py`, `backend/app/pipeline.py`

**One malformed URL can't take down the board.** Source URLs come from live
search results and can be schemeless; `new URL()` throws on those, and thrown
during render it replaced the entire results view with an error boundary — after
a full run had streamed. → `web/src/lib/url.ts`

**The stream tolerates a bad frame.** The SSE parser handles CRLF framing and
skips an unparseable frame instead of ending the run.
→ `web/src/lib/stream.ts`

**Cost is bounded on public endpoints.** Entity count per run is capped,
duplicate AI-usage submissions are deduped before fanning out to paid research,
request payloads are size-limited, and the service is capped at two instances.

**Precedent citations match their category.** Business-name findings were
citing a *title*-injunction case. Each category now carries a citation that
actually bears on it. → `backend/app/pipeline.py`

**Depiction is characterized before it's graded.** The assessor must classify a
depiction as neutral, negative, or disparaging first — a brand shown as stolen
or counterfeit is a tarnishment risk and cannot be waved through as incidental.

**Tests.** 15 cases covering evidence flattening, verdict clamping, the E&O
checklist mapping, request validation, and a full pipeline run streamed through
the real SSE endpoint. → `backend/tests/`

**Accessibility and legibility.** The AI-usage intake was keyboard- and
screen-reader-inaccessible (`display:none` checkboxes). Run status is now
announced via a live region, errors carry `role="alert"`, mode buttons expose
`aria-pressed`, and small text that failed WCAG AA contrast was raised —
it needs to be readable on a projector.

**Honest status.** The header reported "parallel live" before the health check
resolved, including when it failed. It is now tri-state.

**Honest naming.** The output is a *triage* report, matching the scope
disclaimer, rather than a "final" one.

**Mobile.** The header and stage rail overflowed a 375px viewport by 40%.

## Open

- **Adaptive research hop.** When a search returns nothing, have the model
  reformulate the objective and re-search once, surfacing that decision as a
  visible event. This is the most substantive item remaining.
- **Recall table.** The sample script is salted with known clearance landmines;
  publishing caught-vs-planted per category, across repeated runs, would put a
  number on the false-negative rate instead of an assurance.
- **Registry-pinned sources.** Constrain research to authoritative domains
  (`uspto.gov`, PRO repertories) so evidence ranks by authority, not just relevance.
- **Close the loop.** Propose a substitution, then verify the substitute is
  itself clear before offering it.
- **Nested findings.** One underlying issue (a film clip and the performers
  within it) can surface as several line items with unexplained score spread.
- **Parallel retry/backoff** on rate limits, and richer failure surfacing when
  research degrades.
