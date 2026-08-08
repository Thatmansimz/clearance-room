# 🎬 ClearanceRoom

**Every frame cleared.** A deterministic, multi-step clearance agent for film & TV scripts — built on **Gemini (Vertex AI) + Google ADK**, with the **Parallel Search API** as its research engine.

> Before any script shoots, studios pay $1,500–$3,000 and wait 1–2 weeks for a **script clearance report**: every brand, real person, song, artwork, clip, and location must be researched for legal risk. ClearanceRoom does it in about a minute, with live web evidence and per-item production guidance.

Built for the **Agentic Cinema** hackathon — **Parallel track**.

## How it works

A fixed four-stage pipeline (orchestrated in code — deterministic by construction, not left to model whim):

| Stage | Engine | What happens |
|---|---|---|
| 1 · Breakdown | **Gemini via Google ADK** (`LlmAgent`, structured output) | Extracts every clearable entity from the screenplay with scene + usage context |
| 2 · Research | **Parallel Search API** (`POST /v1/search`) | Deterministic fan-out: each entity gets clearance-specific research objectives; live web evidence comes back as ranked excerpts |
| 3 · Assessment | **Gemini** (`google-genai`, JSON schema output) | Scores each entity against its evidence: CLEAR / CAUTION / BLOCKED, risk score, rationale, concrete fix |
| 4 · Report | **Gemini via Google ADK** | Compiles the executive clearance report for the producer |

Every step streams to the UI over SSE — you watch the breakdown land, research fan out, and verdicts get stamped in real time.

### Runtime integration points (for judges)

- **Google Cloud / Gemini / ADK**: [`backend/app/pipeline.py`](backend/app/pipeline.py) — `google.adk.agents.LlmAgent` + `InMemoryRunner` (stages 1 & 4), `google.genai.Client` on Vertex AI (stage 3).
- **Parallel Search API**: [`backend/app/parallel_client.py`](backend/app/parallel_client.py) — `POST https://api.parallel.ai/v1/search`, called once per extracted entity at runtime.

## Run it

```bash
# backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # MOCK_MODE=1 works with no keys
.venv/bin/uvicorn app.main:app --port 8801

# frontend (second terminal)
cd web
npm install
npm run dev -- --port 5177  # proxies /api → :8801
```

Open http://localhost:5177, hit **RUN CLEARANCE** on the bundled sample script *MIDNIGHT STATIC* — a short noir deliberately stuffed with clearance landmines (a sung Beatles song, a Casablanca clip on a diner TV, a Banksy mural as a story beat, famous-brand wardrobe…).

### Live mode

```bash
# .env
MOCK_MODE=0
GOOGLE_CLOUD_PROJECT=<your-project>   # then: gcloud auth application-default login
PARALLEL_API_KEY=<key from platform.parallel.ai>
```

## Stack

Gemini on Vertex AI · Google ADK (Agent Development Kit) · Parallel Search API · FastAPI + SSE · React + Vite + Tailwind

## Roadmap

- Deploy the pipeline to **Vertex AI Agent Engine**; host the app on **Cloud Run**
- PDF export of the clearance report (E&O submission format)
- Scene-level heat map + revised-draft diff view

## License

[MIT](LICENSE)
