# The 3-Minute Trailer — production package

Devpost rules: a demo video showing the agent **functioning as built** (not a
cinematic trailer), public on YouTube/Vimeo, English. Hard cap 3:00. Strategy:
15 seconds of documented pain buys 165 seconds of the product visibly working.

Measured live timings (from real runs, plan the edit around these):
- Script Clearance full run: **~65s** (keep mostly real time — credibility beat)
- TitleGuard "SITUATIONSHIPS": **~40s** (timelapse to ~10s, clock visible)
- True-Story Shield full run: **~106s** (pre-run, show results; one claim ramped)
- AI insurability check (2 usages): **~30s** (timelapse to ~8s)

---

## Shot list + narration

Narration budget ~400 words at a natural pace. VO lines below are written to be
read aloud. Numbers are spelled the way you'd say them.

### 0:00–0:15 · COLD OPEN — the hook
**Screen:** Black. Two headline cards, quick cuts (Billboard's T.I. story,
Deadline's Baby Reindeer story — show as browser screenshots with URLs visible).
**VO:** "Last year a judge blocked T.I.'s finished film from release. Not over
the story. Over the title. And right now Netflix is fighting a hundred-and-
seventy-million-dollar lawsuit over five words: this is a true story."

### 0:15–0:35 · SETUP — the problem, the product
**Screen:** ClearanceRoom landing page at the **prod URL** (address bar
visible). Slow scroll over the script panel.
**VO:** "Every script has landmines like these. Studios pay up to three
thousand dollars and wait two weeks for a clearance report to find them. And
nothing ships without one. No clearance, no insurance. No insurance, no
distribution. This is ClearanceRoom, a deterministic multi-step agent built on
Gemini, Google's Agent Development Kit, and the Parallel Search API. Watch it
work."

### 0:35–1:30 · MAIN DEMO — Script Clearance, live and mostly uncut
**Screen:** Click RUN CLEARANCE on MIDNIGHT STATIC. Let it run. Keep the
sources ticker and the REC dot in frame. Zoom in on cards as verdicts stamp.
**VO (paced over the run):** "This is Midnight Static, a short script we salted
with real landmines. One click. Stage one, Gemini breaks the script down into
every clearable item. Now Parallel fans out live web research on each one. Real
searches, real sources, streaming in. There's the Beatles song, blocked, six
figures to license. The Casablanca clip on the diner TV, blocked, and it cites
Warner's actual clip licensing page. And here's my favorite. We invented a
diner called the Blue Comet. The agent found a real Blue Comet Diner that
operated in Pennsylvania for eighty years. Canned assumptions say fictional.
Live research says lawsuit."
**Beat (zoom on the banner):** "Sixty-three seconds. Fourteen live searches.
Fifty-one sources. A human report takes a week."

### 1:30–1:55 · TITLEGUARD — reproduce the judge's finding
**Screen:** Type SITUATIONSHIPS into TitleGuard. Timelapse the sweep (clock
inset). Zoom: BLOCKED stamp, risk 95, the Justia trademark row, the injunction
source link. Then the cleared alternates appearing.
**VO:** "Remember T.I.'s movie? Type its title into TitleGuard. Blocked, risk
ninety-five. It found the trademark filing, the 2016 web series, and the
injunction itself. It just reproduced a federal judge's finding in forty
seconds. Then it proposes safer titles and screens each one live."

### 1:55–2:20 · TRUE-STORY SHIELD — the $170M check
**Screen:** Switch modes. STATIC & LIGHTNING pre-run results on screen. Zoom
the Nobel claim card (BLOCKED, britannica.com), then the arson claim (BLOCKED,
CRIMINAL badge).
**VO:** "For fact-based films, True-Story Shield reads every claim your script
makes about a real person and checks it against the public record. Our Tesla
biopic says he won the Nobel Prize. Blocked. Britannica says he never did. It
says he burned his own lab for insurance. Blocked. No record supports it. That
exact gap, a damaging claim with no public record, is what the Baby Reindeer
lawsuit turns on."

### 2:20–2:45 · E&O BINDER — the closing frame
**Screen:** Scroll to the E&O Binder. Rows visible with statuses. Check two AI
usages, CHECK INSURABILITY (timelapse), row twelve flips. Click EXPORT PDF,
show the print preview for two seconds.
**VO:** "It all lands where producers live. The E-and-O Binder maps every
finding onto the twelve clearance procedures real underwriters require, in
their own application language, cited. Disclose your AI usage and it checks
current carrier exclusions, live. Then export the report. That's the document
that unlocks insurance, financing, and release."

### 2:45–3:00 · CLOSE
**Screen:** End card: logo, prod URL, repo URL, "Gemini · Google ADK · Parallel
Search API".
**VO:** "One script goes in. The document that clears your film comes out.
ClearanceRoom. Built on Gemini and Parallel. Every frame cleared."

---

## Recording run-sheet

Pre-flight (do all of these before rolling):
1. `curl <prod>/api/health` — confirm `mock_gemini: false, mock_parallel: false`.
2. Record against the **prod URL**, not localhost — the address bar is proof of
   the hosted requirement. Header must show **PARALLEL LIVE** and no mock badges.
3. Do one warm-up run of each demo off camera (confirms timings, warms Cloud Run).
4. Full-screen browser, hide bookmarks bar, close other tabs, mute notifications
   (macOS Focus mode), hide the dock.
5. Never show `.env`, keys, or the GCP console.

Takes (record each as its own clip, real time — compress in edit):
- Take 1: landing page scroll (15s)
- Take 2: full clearance run, uncut, click to report (~90s)
- Take 3: TitleGuard SITUATIONSHIPS, click to alternates (~60s)
- Take 4: mode switch + fact-check run on STATIC & LIGHTNING, uncut (~2min) —
  in the edit you only show the last 25s of results
- Take 5: E&O Binder scroll + AI intake + EXPORT PDF (~60s)
- Take 6 (b-roll): the two headline pages for the cold open

Tools: Screen Studio (nicest auto-zoom) or QuickTime screen recording + CapCut/
DaVinci for the edit. 1080p minimum. Record VO separately over the cut — one
pass, conversational, like you're showing a colleague, not presenting.

Edit notes:
- When you timelapse a wait, keep a small clock overlay running — judges trust
  visible time.
- The clearance run is the credibility centerpiece: resist cutting it below
  ~45s of real time.
- Captions on (YouTube auto-captions are fine, spot-check names: Parallel,
  Vertex, E&O).

## Upload checklist
- YouTube, **public** (not unlisted), English.
- Title: "ClearanceRoom — script clearance agent (Gemini + Google ADK + Parallel Search)"
- Description: prod URL, repo URL, one-paragraph what-it-does, partner track:
  Parallel. Timestamps for each segment.
- Watch it back once on a phone — if a verdict stamp isn't legible there, zoom
  it in the edit.
