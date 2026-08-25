"""
pcn-ingestor — Gmail Push Receiver
===================================
Receives Pub/Sub push notifications from Gmail API users.watch(), fetches
new messages, extracts PDF attachments and uploads them to GCS to trigger
the Eventarc → pcn-agent pipeline.

Auth model:
  - Inbound requests: OIDC token from Pub/Sub (verified via google-auth).
  - Gmail API calls: OAuth 2.0 with a stored refresh token (env vars).
  - Firestore / GCS calls: Application Default Credentials (ADC).
"""

import base64
import json
import logging
import os

from fastapi import FastAPI, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.cloud import firestore, storage
from google.oauth2 import credentials as oauth2_credentials
from google.oauth2 import id_token
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
SERVICE_URL = os.environ.get("SERVICE_URL", "")  # Own Cloud Run URL for OIDC audience check
GCS_RAW_DOCUMENTS_BUCKET = os.environ["GCS_RAW_DOCUMENTS_BUCKET"]

GMAIL_CLIENT_ID = os.environ["GMAIL_CLIENT_ID"]
GMAIL_CLIENT_SECRET = os.environ["GMAIL_CLIENT_SECRET"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]

TOKEN_URI = "https://oauth2.googleapis.com/token"
FIRESTORE_SYNC_COLLECTION = "gmail_sync_state"
FIRESTORE_SYNC_DOC = "state"

# ---------------------------------------------------------------------------
# Shared clients (initialised once at startup)
# ---------------------------------------------------------------------------
app = FastAPI(title="pcn-ingestor")

_db: firestore.Client = None
_gcs: storage.Client = None
_gmail = None


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


def get_gmail():
    """Build an authenticated Gmail API client using the stored refresh token."""
    global _gmail
    if _gmail is None:
        creds = oauth2_credentials.Credentials(
            token=None,
            refresh_token=GMAIL_REFRESH_TOKEN,
            token_uri=TOKEN_URI,
            client_id=GMAIL_CLIENT_ID,
            client_secret=GMAIL_CLIENT_SECRET,
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        _gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return _gmail


# ---------------------------------------------------------------------------
# OIDC token verification helper
# ---------------------------------------------------------------------------
def verify_oidc_token(authorization_header: str) -> None:
    """
    Verify the Bearer OIDC token issued by Pub/Sub for the configured push SA.
    Raises HTTPException(401) on failure.
    """
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

    token = authorization_header.split("Bearer ", 1)[1]
    try:
        request = google_requests.Request()
        # Audience is this service's own URL (set as SERVICE_URL env var at deploy time).
        # In development without SERVICE_URL, skip strict audience check.
        audience = SERVICE_URL if SERVICE_URL else None
        id_token.verify_oauth2_token(token, request, audience=audience)
    except Exception as exc:
        logger.warning("OIDC token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid OIDC token") from exc


# ---------------------------------------------------------------------------
# Firestore helpers for last_history_id
# ---------------------------------------------------------------------------
def get_last_history_id() -> str | None:
    doc_ref = get_db().collection(FIRESTORE_SYNC_COLLECTION).document(FIRESTORE_SYNC_DOC)
    doc = doc_ref.get()
    if doc.exists:
        return str(doc.to_dict().get("last_history_id", ""))
    return None


def set_last_history_id(history_id: str) -> None:
    doc_ref = get_db().collection(FIRESTORE_SYNC_COLLECTION).document(FIRESTORE_SYNC_DOC)
    doc_ref.set({"last_history_id": history_id}, merge=True)


# ---------------------------------------------------------------------------
# Gmail fetch helpers
# ---------------------------------------------------------------------------
def get_new_message_ids(gmail, start_history_id: str) -> list[str]:
    """
    Use users.history.list to find message IDs added to INBOX since start_history_id.
    Returns a list of Gmail message IDs.
    """
    message_ids = []
    try:
        response = (
            gmail.users()
            .history()
            .list(
                userId="me",
                startHistoryId=start_history_id,
                historyTypes=["messageAdded"],
                labelId="INBOX",
            )
            .execute()
        )
        for record in response.get("history", []):
            for added in record.get("messagesAdded", []):
                msg_id = added.get("message", {}).get("id")
                if msg_id:
                    message_ids.append(msg_id)
    except Exception as exc:
        logger.error("history.list failed (startHistoryId=%s): %s", start_history_id, exc)
    return message_ids


def get_message(gmail, message_id: str) -> dict:
    """Fetch full message payload."""
    return gmail.users().messages().get(userId="me", id=message_id, format="full").execute()


def extract_pdf_attachment(gmail, message: dict) -> tuple[bytes, str] | tuple[None, None]:
    """
    Walk the message parts tree looking for application/pdf.
    Returns (pdf_bytes, filename) or (None, None).
    """
    def walk_parts(parts):
        for part in parts:
            if part.get("mimeType") == "application/pdf":
                attachment_id = part.get("body", {}).get("attachmentId")
                filename = part.get("filename", "attachment.pdf")
                if attachment_id:
                    att = (
                        gmail.users()
                        .messages()
                        .attachments()
                        .get(userId="me", messageId=message["id"], id=attachment_id)
                        .execute()
                    )
                    data = att.get("data", "")
                    pdf_bytes = base64.urlsafe_b64decode(data + "==")
                    return pdf_bytes, filename
            # Recurse into sub-parts
            sub_parts = part.get("parts", [])
            if sub_parts:
                result = walk_parts(sub_parts)
                if result[0] is not None:
                    return result
        return None, None

    payload = message.get("payload", {})
    parts = payload.get("parts", [])
    if parts:
        return walk_parts(parts)
    return None, None


# ---------------------------------------------------------------------------
# GCS upload
# ---------------------------------------------------------------------------
def upload_pdf_to_gcs(pdf_bytes: bytes, object_name: str) -> None:
    bucket = get_gcs().bucket(GCS_RAW_DOCUMENTS_BUCKET)
    blob = bucket.blob(object_name)
    blob.upload_from_string(pdf_bytes, content_type="application/pdf")
    logger.info("Uploaded %s to gs://%s/%s", object_name, GCS_RAW_DOCUMENTS_BUCKET, object_name)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.post("/")
async def receive_push(request: Request):
    """
    Accepts a Pub/Sub push message from Gmail API users.watch().
    Verifies OIDC token, fetches new Gmail messages, extracts PDF
    attachments and uploads them to GCS.
    Always returns 200 OK to prevent Pub/Sub retry storms.
    """
    # 1. Verify OIDC token
    auth_header = request.headers.get("Authorization", "")
    verify_oidc_token(auth_header)

    # 2. Parse Pub/Sub envelope
    try:
        body = await request.json()
        message = body.get("message", {})
        raw_data = message.get("data", "")
        payload = json.loads(base64.b64decode(raw_data + "==").decode("utf-8"))
        push_history_id = str(payload.get("historyId", ""))
        email_address = payload.get("emailAddress", "")
    except Exception as exc:
        logger.error("Failed to parse Pub/Sub envelope: %s", exc)
        # Return 200 to ack — malformed message would retry forever otherwise
        return {"status": "error", "detail": "malformed payload"}

    logger.info("Push received for %s, historyId=%s", email_address, push_history_id)

    # 3. Determine starting historyId (Firestore first, fall back to push payload)
    stored_history_id = get_last_history_id()
    start_history_id = stored_history_id if stored_history_id else push_history_id

    # 4. Fetch new messages
    gmail = get_gmail()
    message_ids = get_new_message_ids(gmail, start_history_id)

    if not message_ids:
        logger.info("No new messages found since historyId=%s", start_history_id)
        # Still advance the stored historyId to the push value
        if push_history_id and push_history_id > start_history_id:
            set_last_history_id(push_history_id)
        return {"status": "ok", "messages_processed": 0}

    processed = 0
    latest_history_id = push_history_id

    for msg_id in message_ids:
        try:
            msg = get_message(gmail, msg_id)
            pdf_bytes, filename = extract_pdf_attachment(gmail, msg)

            if pdf_bytes is None:
                logger.info("Message %s has no PDF attachment, skipping", msg_id)
                continue

            object_name = f"{msg_id}.pdf"
            upload_pdf_to_gcs(pdf_bytes, object_name)
            processed += 1
            logger.info("Processed message %s → %s (%d bytes)", msg_id, filename, len(pdf_bytes))
        except Exception as exc:
            logger.error("Error processing message %s: %s", msg_id, exc)

    # 5. Update stored historyId
    if latest_history_id:
        set_last_history_id(latest_history_id)

    return {"status": "ok", "messages_processed": processed}


@app.get("/healthz")
def healthz():
    return {"status": "healthy"}
