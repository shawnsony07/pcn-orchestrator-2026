"""
agent/main.py — Eventarc Handler + ADK PCN Triage Agent
=========================================================
Receives GCS finalize CloudEvents from Eventarc, verifies the OIDC token,
runs the PCN triage agent against the uploaded PDF, and persists results
to Firestore.

Auth model:
  - Inbound requests: OIDC token from Eventarc (verified via google-auth).
  - Firestore / GCS calls: Application Default Credentials (ADC).
  - Vertex AI / ADK: ADC, project+region from env vars.
"""

import logging
import os
from datetime import datetime, timezone

import vertexai
from fastapi import FastAPI, HTTPException, Request
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.auth.transport import requests as google_requests
from google.cloud import firestore, storage
from google.oauth2 import id_token

from tools import generate_eco_pdf, github_create_pr, query_firestore_inventory

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
GCP_PROJECT_ID = os.environ["GCP_PROJECT_ID"]
GCP_REGION = os.environ["GCP_REGION"]
# Strip trailing slash — Eventarc OIDC tokens may be issued with the trailing-slash
# form of the Cloud Run URL; normalise once here so both forms are accepted.
SERVICE_URL = os.environ.get("SERVICE_URL", "").rstrip("/")
GCS_RAW_DOCUMENTS_BUCKET = os.environ["GCS_RAW_DOCUMENTS_BUCKET"]
GITHUB_TARGET_REPO = os.environ.get("GITHUB_TARGET_REPO", "")

AGENT_RUNS_COLLECTION = "agent_runs"
MAX_PDF_BYTES = 5 * 1024 * 1024  # 5 MB cost guard

# ---------------------------------------------------------------------------
# Vertex AI init (once at module load)
# ---------------------------------------------------------------------------
# ADK 2.0 defaults to the public Gemini API (needs an API key).
# Setting this env var before any ADK/genai import forces it to use Vertex AI
# (authenticated via ADC / the attached service account on Cloud Run).
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", GCP_PROJECT_ID)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", GCP_REGION)

vertexai.init(project=GCP_PROJECT_ID, location=GCP_REGION)

# ---------------------------------------------------------------------------
# ADK Agent definition
# ---------------------------------------------------------------------------
TRIAGE_INSTRUCTION = """
You are an autonomous PCN (Product Change Notification) triage agent. Your job is to:

1. Read the PCN document (provided as a GCS URI) and extract:
   - The affected part number(s).
   - The nature of the change (EOL, replacement, spec change, packaging change, etc.).
   - The recommended replacement part number(s) if provided.
   - The timeline/effective date.

2. For each affected part number, call query_firestore_inventory to check if it exists
   in the internal inventory and retrieve any known replacement mappings.

3. Based on the PCN content and inventory data, determine which HAL (Hardware Abstraction
   Layer) header files in the target repository need updating to reference the replacement
   part. Generate the minimal, correct HAL changes.

4. Call github_create_pr with:
   - repo_url: the target GitHub repository URL provided in your prompt.
   - branch: a descriptive branch name like "pcn/<part-number>-replacement".
   - hal_modifications: a dict of file paths to their updated content.

5. Call generate_eco_pdf with a comprehensive ECO report string that includes:
   - Part number affected.
   - PCN summary.
   - Inventory status.
   - Replacement recommendation.
   - HAL files changed.
   - PR link.

6. Do not ask for user confirmation at any step. Act autonomously and completely.
   Report your actions and findings at the end.
"""

# Use the 'vertexai/' prefix so ADK unambiguously routes through Vertex AI,
# even if GOOGLE_GENAI_USE_VERTEXAI is somehow not picked up.
agent = Agent(
    name="pcn_triage_agent",
    model="vertexai/gemini-2.0-flash",
    instruction=TRIAGE_INSTRUCTION,
    tools=[query_firestore_inventory, github_create_pr, generate_eco_pdf],
)

# ADK runner with in-memory session service (stateless per invocation)
session_service = InMemorySessionService()
runner = Runner(agent=agent, app_name="pcn_triage", session_service=session_service)

# ---------------------------------------------------------------------------
# Shared GCP clients
# ---------------------------------------------------------------------------
_db: firestore.Client = None
_gcs: storage.Client = None


def get_db() -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def get_gcs() -> storage.Client:
    global _gcs
    if _gcs is None:
        _gcs = storage.Client()
    return _gcs


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="pcn-agent")


# ---------------------------------------------------------------------------
# OIDC token verification helper
# ---------------------------------------------------------------------------
def verify_oidc_token(authorization_header: str) -> None:
    """Verify Eventarc-issued OIDC Bearer token. Raises HTTPException(401) on failure."""
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

    token = authorization_header.split("Bearer ", 1)[1]
    try:
        request = google_requests.Request()
        if SERVICE_URL:
            try:
                id_token.verify_oauth2_token(token, request, audience=SERVICE_URL)
            except Exception:
                # Retry with trailing slash — Eventarc may issue either form
                id_token.verify_oauth2_token(token, request, audience=SERVICE_URL + "/")
        else:
            id_token.verify_oauth2_token(token, request, audience=None)
    except Exception as exc:
        logger.warning("OIDC token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid OIDC token") from exc


# ---------------------------------------------------------------------------
# Firestore helper — persist agent run
# ---------------------------------------------------------------------------
def save_agent_run(gcs_uri: str, target_repo: str, response: str, status: str) -> None:
    get_db().collection(AGENT_RUNS_COLLECTION).add(
        {
            "gcs_uri": gcs_uri,
            "target_repo": target_repo,
            "response": response,
            "status": status,
            "timestamp": firestore.SERVER_TIMESTAMP,
        }
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.post("/")
async def receive_event(request: Request):
    """
    Receives a GCS finalize CloudEvent from Eventarc.

    Expected headers:
      - Authorization: Bearer <oidc-token>
      - ce-subject: objects/<object-name>   (set by Eventarc for GCS events)

    Always returns 200 OK (Eventarc retries on non-2xx).
    """
    # 1. OIDC verification
    auth_header = request.headers.get("Authorization", "")
    verify_oidc_token(auth_header)

    # 2. Extract GCS object name from the ce-subject header
    # Eventarc sets ce-subject to e.g. "objects/<name>" or "objects/<name>?generation=..."
    ce_subject = request.headers.get("ce-subject", "")
    if not ce_subject:
        # Also check the CloudEvent JSON body as fallback
        try:
            body = await request.json()
            ce_subject = body.get("subject", "")
        except Exception:
            pass

    object_name = ce_subject.replace("objects/", "").split("?")[0].strip()
    if not object_name:
        logger.error("Could not determine GCS object name from ce-subject: %s", ce_subject)
        return {"status": "error", "detail": "missing ce-subject"}

    gcs_uri = f"gs://{GCS_RAW_DOCUMENTS_BUCKET}/{object_name}"
    logger.info("Received Eventarc event for %s", gcs_uri)

    # 3. Cost guard — check PDF size
    try:
        bucket = get_gcs().bucket(GCS_RAW_DOCUMENTS_BUCKET)
        blob = bucket.blob(object_name)
        blob.reload()
        blob_size = blob.size or 0
    except Exception as exc:
        logger.error("Could not read blob metadata for %s: %s", object_name, exc)
        save_agent_run(gcs_uri, GITHUB_TARGET_REPO, f"Metadata read failed: {exc}", "REJECTED_METADATA_ERROR")
        return {"status": "ok", "detail": "rejected: could not read blob metadata"}

    if blob_size > MAX_PDF_BYTES:
        logger.warning("Blob %s is %d bytes, exceeds 5MB limit — rejecting", object_name, blob_size)
        save_agent_run(gcs_uri, GITHUB_TARGET_REPO, "Payload Too Large", "REJECTED_SIZE")
        return {"status": "ok", "detail": "rejected: payload too large"}

    # 4. Run the ADK agent
    prompt = (
        f"A new PCN document has been uploaded to {gcs_uri}.\n"
        f"Target repository for HAL updates: {GITHUB_TARGET_REPO}\n\n"
        "Triage this PCN autonomously: identify affected parts, check inventory, "
        "open a GitHub PR with necessary HAL changes, and generate the ECO PDF report."
    )

    session_id = f"pcn-{object_name}-{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    try:
        from google.genai import types as genai_types
        user_message = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=prompt)],
        )

        # InMemorySessionService.create_session is a coroutine — must be awaited.
        # Creating it first is required before runner.run_async() looks it up.
        await session_service.create_session(
            app_name="pcn_triage",
            user_id="system",
            session_id=session_id,
        )

        # Use run_async (async for) since we are already inside an async FastAPI
        # endpoint — avoids the background-thread / event-loop conflict.
        final_response = ""
        async for event in runner.run_async(
            user_id="system",
            session_id=session_id,
            new_message=user_message,
        ):
            if hasattr(event, "content") and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        final_response += part.text

        status = "COMPLETED"
        logger.info("Agent run completed for %s", gcs_uri)

    except Exception as exc:
        logger.error("Agent run failed for %s: %s", gcs_uri, exc)
        final_response = f"Agent run failed: {exc}"
        status = "ERROR"

    # 5. Persist result to Firestore
    save_agent_run(gcs_uri, GITHUB_TARGET_REPO, final_response, status)

    return {
        "status": "ok",
        "gcs_uri": gcs_uri,
        "agent_status": status,
        "summary": final_response[:500] if final_response else "",
    }


@app.get("/healthz")
def healthz():
    return {"status": "healthy"}
