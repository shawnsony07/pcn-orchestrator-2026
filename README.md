# PCN Triage Orchestrator

An autonomous PCN (Product Change Notification) triage pipeline built for the
**All Things Agentic Hackathon 2026** using Google ADK 2.0, Gemini, Eventarc,
Gmail API, Pub/Sub, Firestore, and Cloud Run.

## Architecture

```
Gmail inbox (watched address)
    │  users.watch() push notification
    ▼
Pub/Sub topic (gmail-pcn-notifications)
    │  push subscription
    ▼
Cloud Run: pcn-ingestor
    │  extracts PDF attachment
    │  uploads to GCS (pcn-raw-documents)
    ▼
Eventarc trigger (GCS finalize)
    │
    ▼
Cloud Run: pcn-agent
    │  ADK Agent (gemini-3.5-flash)
    │  ├─ query_firestore_inventory  → Firestore (inventory collection)
    │  ├─ github_create_pr           → GitHub (HAL header updates)
    │  └─ generate_eco_pdf           → GCS (eco-outputs bucket)
    ▼
Firestore: agent_runs collection (audit log)
```

---

## Prerequisites

1. **gcloud CLI** — [Install](https://cloud.google.com/sdk/docs/install), then:
   ```bash
   gcloud auth login
   gcloud config set project <your-gcp-project-id>
   ```
2. **Python 3.11+**
3. **A GCP project** with the infrastructure already provisioned (see AGENTS.md §0).

---

## Local Setup

### 1. Clone and create `.env`

```bash
git clone https://github.com/<your-org>/pcn-orchestrator-2026.git
cd pcn-orchestrator-2026
cp .env.example .env
```

Edit `.env` and fill in all values:

| Variable | Description |
|---|---|
| `GCP_PROJECT_ID` | Your GCP project ID |
| `GCP_REGION` | GCP region where services are deployed (e.g. `asia-south1`) |
| `GITHUB_TOKEN` | Fine-grained PAT with `Contents: write` + `Pull requests: write` on the target repo |
| `GITHUB_TARGET_REPO` | Target repo for HAL updates (`owner/repo` or full HTTPS URL) |
| `GMAIL_CLIENT_ID` | OAuth 2.0 client ID from GCP Console |
| `GMAIL_CLIENT_SECRET` | OAuth 2.0 client secret |
| `GMAIL_REFRESH_TOKEN` | Refresh token obtained by running `scripts/gmail_oauth_setup.py` |
| `GMAIL_WATCHED_ADDRESS` | Gmail address to watch for PCN emails |
| `GMAIL_PUBSUB_TOPIC` | Full Pub/Sub topic name (`projects/<project>/topics/<topic>`) |
| `GCS_RAW_DOCUMENTS_BUCKET` | GCS bucket name for raw PDF uploads |
| `GCS_ECO_OUTPUTS_BUCKET` | GCS bucket name for generated ECO PDF outputs |
| `SERVICE_URL` | Each service's own Cloud Run URL (used as OIDC token audience) |

### 2. Authenticate with ADC (impersonate service account)

```bash
gcloud auth application-default login \
  --impersonate-service-account=pcn-agent-sa@pcn-orchestrator-2026.iam.gserviceaccount.com
```

> **Note:** No service account key files are used or created. ADC impersonation is the only
> supported auth method for local development (an org policy blocks key creation).

### 3. Run services locally

**Ingestor:**
```bash
cd ingestor
pip install -r requirements.txt
set -a && source ../.env && set +a   # or use dotenv
uvicorn main:app --reload --port 8081
```

**Agent:**
```bash
cd agent
pip install -r requirements.txt
set -a && source ../.env && set +a
uvicorn main:app --reload --port 8082
```

To test the ingestor locally, you can POST a mock Pub/Sub envelope:
```bash
curl -X POST http://localhost:8081/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <valid-oidc-token>" \
  -d '{"message": {"data": "<base64-encoded-json>", "messageId": "1", "publishTime": "2026-01-01T00:00:00Z"}, "subscription": "projects/pcn-orchestrator-2026/subscriptions/gmail-pcn-sub"}'
```

---

## GCP Deployment

Both services use `--no-allow-unauthenticated` (locked down). Deploy with:

```bash
# Deploy ingestor
gcloud run deploy pcn-ingestor \
  --source ./ingestor \
  --region asia-south1 \
  --no-allow-unauthenticated \
  --project pcn-orchestrator-2026 \
  --set-env-vars="GCS_RAW_DOCUMENTS_BUCKET=pcn-raw-documents,GMAIL_CLIENT_ID=...,GMAIL_CLIENT_SECRET=...,GMAIL_REFRESH_TOKEN=...,SERVICE_URL=<ingestor-url>"

# Deploy agent
gcloud run deploy pcn-agent \
  --source ./agent \
  --region asia-south1 \
  --no-allow-unauthenticated \
  --project pcn-orchestrator-2026 \
  --set-env-vars="GCP_PROJECT_ID=pcn-orchestrator-2026,GCP_REGION=asia-south1,GCS_RAW_DOCUMENTS_BUCKET=pcn-raw-documents,GCS_ECO_OUTPUTS_BUCKET=eco-outputs,GITHUB_TOKEN=...,GITHUB_TARGET_REPO=...,SERVICE_URL=<agent-url>"
```

> IAM bindings granting `pcn-agent-sa` the `roles/run.invoker` role on both services
> are already in place and do not need to be redone.

---

## Gmail Watch Renewal

`users.watch()` expires every **~7 days**. Until the Cloud Scheduler job is set up,
run the renewal script manually each week:

```bash
cd scripts
pip install google-auth google-api-python-client
set -a && source ../.env && set +a
python gmail_watch_renew.py
```

**Known limitation:** Automated weekly renewal via Cloud Scheduler + Cloud Run Job is
documented in `scripts/gmail_watch_renew.py` but not yet deployed. If the watch expires,
the ingestor stops receiving new email notifications silently. Monitor expiration via the
`expiration` field printed by the renewal script.

Planned Cloud Scheduler setup:
```bash
# Create a Cloud Scheduler job to renew weekly (after packaging as a Cloud Run Job)
gcloud scheduler jobs create http gmail-watch-renew \
  --schedule="0 0 * * 0" \
  --uri="<cloud-run-job-url>" \
  --oidc-service-account-email=pcn-agent-sa@pcn-orchestrator-2026.iam.gserviceaccount.com \
  --location=asia-south1
```

---

## CI/CD

GitHub Actions (`.github/workflows/ci-validation.yml`) runs on every push and PR:
- Installs dependencies for both `ingestor/` and `agent/`
- Runs `flake8` lint on all Python files
- Runs `python -c "import main"` smoke test in each directory

Deployment is **manual** via `gcloud run deploy` — no automated deployment in CI.

---

## Project Structure

```
pcn-orchestrator-2026/
├── ingestor/           # Gmail push receiver → GCS upload
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── agent/              # Eventarc handler + ADK triage agent
│   ├── main.py
│   ├── tools.py
│   ├── requirements.txt
│   └── Dockerfile
├── scripts/
│   ├── gmail_oauth_setup.py     # One-time OAuth setup (already run)
│   ├── gmail_watch.py           # Initial watch registration
│   └── gmail_watch_renew.py    # Weekly renewal script
├── secrets/                     # gitignored, holds OAuth client JSON
├── .github/workflows/
│   └── ci-validation.yml
├── .env.example
└── README.md
```

---

## License

MIT
