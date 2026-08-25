"""
Calls Gmail API users.watch() to start pushing new-mail notifications for
shawngdg2005@gmail.com to the gmail-pcn-notifications Pub/Sub topic.

Gmail watch() expires after 7 days -- this needs to be re-run on a schedule
(Cloud Scheduler -> small Cloud Run job/Cloud Function) to keep it alive.

Setup:
    pip install google-auth google-api-python-client

Usage (reads creds from env vars):
    set GMAIL_CLIENT_ID=...
    set GMAIL_CLIENT_SECRET=...
    set GMAIL_REFRESH_TOKEN=...
    set GCP_PROJECT_ID=pcn-orchestrator-2026
    python gmail_watch.py
"""

import os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_URI = "https://oauth2.googleapis.com/token"


def main():
    client_id = os.environ["GMAIL_CLIENT_ID"]
    client_secret = os.environ["GMAIL_CLIENT_SECRET"]
    refresh_token = os.environ["GMAIL_REFRESH_TOKEN"]
    project_id = os.environ.get("GCP_PROJECT_ID", "pcn-orchestrator-2026")
    topic_name = f"projects/{project_id}/topics/gmail-pcn-notifications"

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )

    service = build("gmail", "v1", credentials=creds)

    request_body = {
        "labelIds": ["INBOX"],
        "topicName": topic_name,
    }

    response = service.users().watch(userId="me", body=request_body).execute()

    print("Watch registered successfully.")
    print(f"  historyId: {response.get('historyId')}")
    print(f"  expiration (epoch ms): {response.get('expiration')}")
    print(
        "\nNOTE: this expires in ~7 days. Set up Cloud Scheduler to re-run "
        "this call weekly, or your ingestor stops receiving new mail silently."
    )


if __name__ == "__main__":
    main()
