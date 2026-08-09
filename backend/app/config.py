"""ClearanceRoom configuration.

Live mode requires:
  - GOOGLE_CLOUD_PROJECT + `gcloud auth application-default login` (Vertex AI)
  - PARALLEL_API_KEY (Parallel Search API)
Mock mode (MOCK_MODE=1) runs the full pipeline with canned data — no keys needed.
"""
import os

from dotenv import load_dotenv

load_dotenv()

MOCK_MODE = os.getenv("MOCK_MODE", "0") == "1"

# Google Cloud / Vertex AI
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_REPORT_MODEL = os.getenv("GEMINI_REPORT_MODEL", "gemini-3.1-pro-preview")

# Vertex AI routing for google-genai and ADK
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
if GOOGLE_CLOUD_PROJECT:
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", GOOGLE_CLOUD_PROJECT)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", GOOGLE_CLOUD_LOCATION)

# Parallel Search API
PARALLEL_API_KEY = os.getenv("PARALLEL_API_KEY", "")
PARALLEL_API_URL = os.getenv("PARALLEL_API_URL", "https://api.parallel.ai/v1/search")
PARALLEL_MODE = os.getenv("PARALLEL_MODE", "advanced")  # "advanced" | "turbo"
# Task API (Deep Dossier): deeper multi-hop research with per-field citations,
# reasoning, and confidence. "core" balances depth against demo-friendly latency.
# "-fast" variants are 2-5x quicker at identical pricing (measured: core-fast
# 28s vs core 150s on the same dossier), which is the difference between a
# dossier you can show on camera and one you have to cut away from.
PARALLEL_TASK_PROCESSOR = os.getenv("PARALLEL_TASK_PROCESSOR", "core-fast")
DOSSIER_POLL_SECONDS = float(os.getenv("DOSSIER_POLL_SECONDS", "5"))
DOSSIER_TIMEOUT_SECONDS = float(os.getenv("DOSSIER_TIMEOUT_SECONDS", "300"))

# Pipeline tuning
RESEARCH_CONCURRENCY = int(os.getenv("RESEARCH_CONCURRENCY", "4"))
# Every entity costs a paid search, so a single run's fan-out is bounded even if
# the breakdown stage returns hundreds of items from a feature-length script.
MAX_ENTITIES_PER_RUN = int(os.getenv("MAX_ENTITIES_PER_RUN", "60"))

# Abuse guards for public, unauthenticated, paid-API-backed endpoints.
MAX_SCRIPT_CHARS = int(os.getenv("MAX_SCRIPT_CHARS", "120000"))
MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "3"))

# Per-service mock flags: MOCK_MODE=1 forces full mock; otherwise each service
# goes live as soon as its credentials are present.
MOCK_GEMINI = MOCK_MODE or not GOOGLE_CLOUD_PROJECT
MOCK_PARALLEL = MOCK_MODE or not PARALLEL_API_KEY
