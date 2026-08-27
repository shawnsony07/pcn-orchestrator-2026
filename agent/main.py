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

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone

import vertexai
from fastapi import FastAPI, HTTPException, Request
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.auth.transport import requests as google_requests
from google.cloud import firestore, storage
from google.genai import types as genai_types
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
STAGE1_INSTRUCTION = """
[TRIAGE]
You are the Triage Agent in an autonomous PCN pipeline. Your ONLY job is to extract part numbers.

1. Read the attached PCN PDF document natively. Do not guess or hallucinate part numbers — use ONLY the part
   numbers and replacement parts explicitly stated in the document text, tables, or diagrams.

   MULTI-PAGE RULE: This document may span multiple pages. A cover page, a table of affected
   parts, and a signature/footer page are common. Read and consider ALL pages before extracting
   part numbers — do not assume all relevant information is on page one.

2. From the document, identify ALL distinct affected part numbers.

Report your actions and findings at the very end as ONLY a valid JSON object matching exactly this schema:
{"parts": ["<part_number_1>", "<part_number_2>", ...]}
If no part numbers can be identified, output {"parts": []}. Do not guess or force an output.
"""

STAGE2_INSTRUCTION = """
[RESOLUTION]
You are the Resolution Agent in an autonomous PCN pipeline.
The user will provide the part numbers extracted by the Triage Agent.

For each part number listed in the triage output, call query_firestore_inventory to check if it
exists in the internal inventory and retrieve any known replacement mappings.

CRITICAL INVENTORY RULE: If query_firestore_inventory returns {"found": false} for a part number,
you MUST NOT invent, guess, or suggest a replacement part under any circumstances.

Output a structured JSON array, one entry per part, with no markdown fences and no surrounding prose:
{"parts": [
  {"part_number": "...", "found": true, "replacement_part_numbers": [...], "status": "..."}
]}
"""

STAGE3_INSTRUCTION = """
[ACTION]
You are the Action Agent in an autonomous PCN pipeline.
The user will provide the inventory status for the parts from the Resolution Agent.

For each part where "found" is true:
1. Determine which HAL (Hardware Abstraction Layer) header files in the target repository need updating
   to reference the replacement part. Generate the minimal, correct HAL changes.
   CRITICAL INSTRUCTION: The file path MUST exactly match the pattern `hal_<part_number_lowercase>.h`.
   Do not invent other filenames or nested paths (e.g. use `hal_bme280.h`).
2. Call github_create_pr to open a PR for that part. Once it returns SUCCESS, DO NOT call it again for the same part.
3. Call generate_eco_pdf to generate the ECO report for that part.

For parts where "found" is false, take no action. Do not call github_create_pr or generate_eco_pdf.

Report your actions and findings at the very end as ONLY a valid JSON object
matching exactly this schema, with no markdown fences and no surrounding prose:
{
    "parts": [
        {
            "part_number": "...",
            "replacement_found": true/false,
            "pr_url": "..." or null,
            "eco_url": "..." or null,
            "status": "COMPLETED" or "NO_INVENTORY_MATCH"
        }
    ]
}
"""

triage_agent = Agent(
    name="triage_agent",
    model="gemini-3.5-flash",
    instruction=STAGE1_INSTRUCTION,
    tools=[],
)

resolution_agent = Agent(
    name="resolution_agent",
    model="gemini-3.5-flash",
    instruction=STAGE2_INSTRUCTION,
    tools=[query_firestore_inventory],
)

action_agent = Agent(
    name="action_agent",
    model="gemini-3.5-flash",
    instruction=STAGE3_INSTRUCTION,
    tools=[github_create_pr, generate_eco_pdf],
)

# Shared session service
session_service = InMemorySessionService()

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


def save_agent_run(
    gcs_uri: str, target_repo: str, response: str, status: str,
    extracted_parts: list = None,
) -> None:
    db = get_db()
    docs = list(db.collection(AGENT_RUNS_COLLECTION).where(
        filter=firestore.FieldFilter("gcs_uri", "==", gcs_uri)).limit(1).stream())
    data = {
        "gcs_uri": gcs_uri,
        "target_repo": target_repo,
        "response": response,
        "status": status,
        "extracted_parts": extracted_parts or [],
        "timestamp": firestore.SERVER_TIMESTAMP,
    }
    if docs:
        docs[0].reference.update(data)
    else:
        db.collection(AGENT_RUNS_COLLECTION).add(data)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def run_stage_with_retry(
    runner: Runner, session_id: str, new_message: genai_types.Content, stage_name: str
) -> str:
    """Executes a runner with up to 3 retries on failure."""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            final_response = ""
            async for event in runner.run_async(
                user_id="system",
                session_id=session_id,
                new_message=new_message,
            ):
                if hasattr(event, "content") and event.content:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            final_response += part.text
            return final_response
        except Exception as exc:
            if attempt == max_attempts - 1:
                logger.error("%s run failed: %s", stage_name, exc)
                raise
            delay = 2 ** attempt
            logger.warning("%s Retry attempt %d/3 after transient error, retrying in %ds: %s",
                           stage_name, attempt + 2, delay, exc)
            await asyncio.sleep(delay)


def extract_json_from_response(response: str) -> dict:
    """Parses JSON defensively, stripping markdown fences if present."""
    json_str = response
    match = re.search(r'```(?:json)?(.*?)```', response, re.DOTALL)
    if match:
        json_str = match.group(1)
    return json.loads(json_str.strip())


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.post("/")
async def receive_event(request: Request):
    """
    Receives a GCS finalize CloudEvent from Eventarc.
    """
    # 1. OIDC verification
    auth_header = request.headers.get("Authorization", "")
    verify_oidc_token(auth_header)

    # 2. Extract GCS object name from the ce-subject header
    ce_subject = request.headers.get("ce-subject", "")
    if not ce_subject:
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

    # 3. Idempotency check
    db = get_db()
    existing_runs = list(db.collection(AGENT_RUNS_COLLECTION).where(
        filter=firestore.FieldFilter("gcs_uri", "==", gcs_uri)).limit(1).stream())
    if existing_runs:
        run_data = existing_runs[0].to_dict()
        existing_status = run_data.get("status")
        logger.info(
            "Duplicate delivery skipped: agent_run already exists for %s with status %s",
            gcs_uri, existing_status
        )
        return {"status": "ok", "detail": "duplicate delivery skipped"}

    # 4. Cost guard — check PDF size
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

    # 5. Mark as IN_PROGRESS to prevent concurrent duplicates
    save_agent_run(gcs_uri, GITHUB_TARGET_REPO, "Pipeline started", "IN_PROGRESS")

    # 6. Run the ADK agents manually
    session_id = f"pcn-{object_name}-{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    extracted_parts = []
    final_response = ""
    status = "COMPLETED"

    try:
        await session_service.create_session(
            app_name="pcn_triage",
            user_id="system",
            session_id=session_id,
        )

        # Stage 1: Triage
        logger.info(f"[TRIAGE] Starting — reading PDF from {gcs_uri}")
        triage_runner = Runner(agent=triage_agent, app_name="pcn_triage", session_service=session_service)
        triage_prompt = (
            f"A new PCN document has been uploaded to {gcs_uri}.\n"
            f"GCS Object Name: {object_name}\n\n"
            "Triage this PCN autonomously: identify all affected parts from the attached PDF."
        )
        triage_message = genai_types.Content(
            role="user",
            parts=[
                genai_types.Part.from_uri(file_uri=gcs_uri, mime_type="application/pdf"),
                genai_types.Part.from_text(text=triage_prompt)
            ],
        )
        triage_response = await run_stage_with_retry(triage_runner, session_id, triage_message, "[TRIAGE]")
        triage_data = extract_json_from_response(triage_response)
        parts = triage_data.get("parts", [])
        logger.info(f"[TRIAGE] Completed — extracted parts: {triage_data}")

        # Stage 2: Resolution
        logger.info(f"[RESOLUTION] Starting — resolving {len(parts)} part(s) against inventory")
        resolution_runner = Runner(agent=resolution_agent, app_name="pcn_triage", session_service=session_service)
        resolution_prompt = (
            f"The Triage Agent extracted the following part numbers:\n"
            f"{json.dumps(triage_data)}\n\n"
            "Call query_firestore_inventory for each part to check if it exists in the internal inventory."
        )
        resolution_message = genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=resolution_prompt)]
        )
        resolution_response = await run_stage_with_retry(
            resolution_runner, session_id, resolution_message, "[RESOLUTION]"
        )
        resolution_data = extract_json_from_response(resolution_response)
        resolved_parts = resolution_data.get("parts", [])
        found_count = sum(1 for p in resolved_parts if p.get("found"))
        not_found_count = len(resolved_parts) - found_count
        logger.info(f"[RESOLUTION] Completed — {found_count} found, {not_found_count} not found")

        # Stage 3: Action
        logger.info(f"[ACTION] Starting — processing {len(resolved_parts)} resolved part(s)")
        action_runner = Runner(agent=action_agent, app_name="pcn_triage", session_service=session_service)
        action_prompt = (
            f"Target repository for HAL updates: {GITHUB_TARGET_REPO}\n"
            f"The Resolution Agent provided this inventory status for the parts:\n"
            f"{json.dumps(resolution_data)}\n\n"
            "Take the appropriate actions (generate PRs and ECOs) for each part where found is true."
        )
        action_message = genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=action_prompt)]
        )
        final_response = await run_stage_with_retry(action_runner, session_id, action_message, "[ACTION]")
        final_data = extract_json_from_response(final_response)
        action_parts = final_data.get("parts", [])
        pr_count = sum(1 for p in action_parts if p.get("pr_url"))
        eco_count = sum(1 for p in action_parts if p.get("eco_url"))
        logger.info(f"[ACTION] Completed — {pr_count} PR(s) opened, {eco_count} ECO(s) generated")

        # Final Parsing and Normalization
        parts_list = final_data.get("parts", [])
        if not isinstance(parts_list, list):
            raise ValueError("'parts' field is not a list")

        has_completed = False
        has_no_match = False
        for entry in parts_list:
            part_status = entry.get("status")
            if part_status is None:
                part_status = "COMPLETED" if entry.get("replacement_found") else "NO_INVENTORY_MATCH"
                entry["status"] = part_status
            if part_status == "COMPLETED":
                has_completed = True
            else:
                has_no_match = True

        extracted_parts = parts_list
        if has_completed:
            status = "COMPLETED"
        elif has_no_match:
            status = "NO_INVENTORY_MATCH"

    except Exception as exc:
        logger.error("Agent run failed for %s: %s", gcs_uri, exc)
        status = "ERROR"
        if not final_response:
            final_response = f"Failed during agent run: {exc}"

    # 6. Persist result to Firestore
    save_agent_run(gcs_uri, GITHUB_TARGET_REPO, final_response, status, extracted_parts)

    return {
        "status": "ok",
        "gcs_uri": gcs_uri,
        "agent_status": status,
        "summary": final_response[:500] if final_response else "",
    }


@app.get("/health")
def health():
    return {"status": "ok"}
