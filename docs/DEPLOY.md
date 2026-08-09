# Deploy runbook — ClearanceRoom on Cloud Run

Redeploy the current `main` and kill the cold-start delay. **Run this from the macOS login that holds the gcloud SDK + Application Default Credentials** (the `michaelwilliamsii` profile) — that's the only place `gcloud` is authenticated for this project.

Service: `clearanceroom` · region `us-central1` · project number `957638696965`
Live URL: https://clearanceroom-957638696965.us-central1.run.app

---

## 0. Get the latest code first

```bash
cd ~/clearance-room
git pull origin main
```

If the pull complains about credentials (the keychain may hand git the wrong GitHub account), push/pull with the gh helper:

```bash
gh auth switch --user Thatmansimz
git -c credential.helper= -c credential.helper='!gh auth git-credential' pull origin main
```

---

## 1. Fastest fix for the cold start (no rebuild)

The "takes me nowhere" symptom is a cold start: the container scales to zero when idle, so the first hit after a quiet spell waits ~15–30s. Keep one instance warm:

```bash
gcloud run services update clearanceroom \
  --region=us-central1 \
  --min-instances=1 \
  --cpu-boost
```

`--min-instances=1` means a judge never hits a cold start. `--cpu-boost` speeds the first request if it ever does. (One always-on instance costs a few dollars a month — fine through judging; set it back to `--min-instances=0` afterward to stop the meter.)

---

## 2. Full redeploy of the latest code

Rebuilds the container from the repo `Dockerfile` (Node build stage → Python runtime, single container serving API + SPA) and ships it:

```bash
cd ~/clearance-room
gcloud run deploy clearanceroom \
  --source . \
  --region=us-central1 \
  --min-instances=1 \
  --cpu-boost \
  --allow-unauthenticated \
  --set-env-vars=MOCK_MODE=0,GOOGLE_CLOUD_PROJECT=<PROJECT_ID>,GEMINI_MODEL=gemini-3.6-flash,GEMINI_REPORT_MODEL=gemini-3.1-pro-preview,GOOGLE_GENAI_USE_VERTEXAI=TRUE,STATIC_DIR=/app/static
```

Confirm `<PROJECT_ID>` with `gcloud config get-value project` (it may differ from the project *number* in the URL).

### The Parallel API key — do NOT put it in the command or the repo

`PARALLEL_API_KEY` is a secret. Set it via Secret Manager, not `--set-env-vars`:

```bash
# one time: store the key
printf '%s' 'YOUR_PARALLEL_KEY' | gcloud secrets create parallel-api-key --data-file=-
# then on deploy (add to the deploy command):
  --set-secrets=PARALLEL_API_KEY=parallel-api-key:latest
```

(If it was originally deployed with the key as a plain env var, it's already set on the service and a `--source` redeploy keeps existing env unless you overwrite it — but Secret Manager is the right home for it.)

---

## 3. Verify before you demo

```bash
curl -s https://clearanceroom-957638696965.us-central1.run.app/api/health | python3 -m json.tool
```

You want:
- `"mock_gemini": false` and `"mock_parallel": false` — **if either is `true`, a key is missing and the app will serve canned data instead of a real run.** Fix the env/secret and redeploy before judging.
- `"models"` shows `gemini-3.6-flash` (breakdown/assess) and `gemini-3.1-pro-preview` (report).

Then open the URL, paste a short script that is NOT the bundled sample, and confirm real Parallel source URLs render per card. That proves it's live, not mocked.

---

## What v0.2 added (already on `main`)

- SSE keepalive (15s) so a long run survives Cloud Run's ~300s idle timeout.
- Concurrency + script-size caps on the public endpoints.
- One failed research/assessment no longer aborts the run — the item degrades and is flagged; the rest complete.
- Ungrounded verdicts (no web evidence) are called out instead of shown as confident research.
- ErrorBoundary so a render fault can't blank the board mid-demo.
- README repositioned as pre-production triage (not a substitute for the clearance report or counsel).
