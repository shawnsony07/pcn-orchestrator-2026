"""
One-time local script to authorize Gmail API access for the watched mailbox
(shawngdg2005@gmail.com) and print a refresh token.

Run this ONCE locally. It opens a browser, you log in as shawngdg2005@gmail.com,
grant access, and this prints the refresh token you need to save as the
GMAIL_REFRESH_TOKEN secret.

Setup before running:
    pip install google-auth-oauthlib google-auth google-api-python-client

Usage:
    python gmail_oauth_setup.py path/to/client_secret_....json
"""

import sys
from google_auth_oauthlib.flow import InstalledAppFlow

# Read-only is enough for watch() + fetching message content/attachments.
# gmail.modify is only needed if you plan to mark messages as read/archived later.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main():
    if len(sys.argv) != 2:
        print("Usage: python gmail_oauth_setup.py path/to/client_secret_....json")
        sys.exit(1)

    client_secret_path = sys.argv[1]

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)

    # This opens a browser window. Log in as shawngdg2005@gmail.com and click Allow.
    creds = flow.run_local_server(port=0)

    print("\n--- SAVE THESE AS ENV VARS / SECRETS ---")
    print(f"GMAIL_CLIENT_ID={creds.client_id}")
    print(f"GMAIL_CLIENT_SECRET={creds.client_secret}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("-----------------------------------------")

    if not creds.refresh_token:
        print(
            "\nWARNING: No refresh token returned. This usually means you've "
            "authorized this app before and Google didn't re-issue one. "
            "Go to https://myaccount.google.com/permissions (while logged in as "
            "shawngdg2005@gmail.com), remove access for this app, and re-run this script."
        )


if __name__ == "__main__":
    main()
