"""
scripts/gmail_watch_renew.py — Non-interactive Gmail Watch Renewal
==================================================================
Renews the Gmail API users.watch() registration for the watched mailbox.
Gmail watch expires every ~7 days. This script is designed to be:
  - Run as a Cloud Run Job triggered weekly by Cloud Scheduler
  - OR run locally (reads credentials from env vars, no browser flow needed)

Usage (reads from env vars):
    python gmail_watch_renew.py

Env vars required:
    GMAIL_CLIENT_ID
    GMAIL_CLIENT_SECRET
    GMAIL_REFRESH_TOKEN
    GCP_PROJECT_ID
    GMAIL_WATCHED_ADDRESS  (informational only, watch is registered for "me")
    GMAIL_PUBSUB_TOPIC     (optional override; defaults to gmail-pcn-notifications)

Cloud Scheduler setup (once infra is ready):
    Schedule: 0 0 * * 0  (every Sunday at midnight UTC)
    Target:   Cloud Run Job that runs this script
"""

import logging
import os
import sys

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"


def renew_watch() -> None:
    client_id = os.environ["GMAIL_CLIENT_ID"]
    client_secret = os.environ["GMAIL_CLIENT_SECRET"]
    refresh_token = os.environ["GMAIL_REFRESH_TOKEN"]
    project_id = os.environ["GCP_PROJECT_ID"]

    # Allow override via env var, fall back to the live topic name
    topic_name = os.environ.get(
        "GMAIL_PUBSUB_TOPIC",
        f"projects/{project_id}/topics/gmail-pcn-notifications",
    )

    watched_address = os.environ.get("GMAIL_WATCHED_ADDRESS", "<not set>")
    logger.info("Renewing Gmail watch for %s → topic %s", watched_address, topic_name)

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    request_body = {
        "labelIds": ["INBOX"],
        "topicName": topic_name,
    }

    response = service.users().watch(userId="me", body=request_body).execute()

    logger.info("Watch renewed successfully.")
    logger.info("  historyId  : %s", response.get("historyId"))
    logger.info("  expiration : %s (epoch ms)", response.get("expiration"))
    print("Watch renewed.")
    print(f"  historyId : {response.get('historyId')}")
    print(f"  expiration: {response.get('expiration')} (epoch ms)")


if __name__ == "__main__":
    try:
        renew_watch()
    except KeyError as exc:
        logger.error("Missing required environment variable: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("Watch renewal failed: %s", exc)
        sys.exit(1)
