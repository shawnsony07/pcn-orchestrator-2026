# PCN Triage Orchestrator

An autonomous PCN (Product Change Notification) triage pipeline using Google ADK 2.0, Gemini 3.5 Flash, Eventarc, Gmail API, Pub/Sub, Firestore, and Cloud Run.

## Overview

The PCN Triage Orchestrator automates the intake and resolution of hardware Product Change Notifications. It seamlessly parses complex multi-page PDF documents, extracts critical component data, cross-references inventory via Firestore, and autonomously proposes GitHub PRs and generates ECO (Engineering Change Order) PDFs for affected components.

---

## Local Development Prerequisites

1. **gcloud CLI** — [Install](https://cloud.google.com/sdk/docs/install), then authenticate:
   ```bash
   gcloud auth login
   gcloud config set project <your-gcp-project-id>
   ```
2. **Python 3.11+**
3. **A GCP project** with the infrastructure listed in the Provisioning section.

## Environment Variables (.env)

Duplicate `.env.example` to `.env` and fill in the values. **Never commit your `.env` file.**

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
| `ALLOWED_SENDERS` | Comma-separated list of authorized sender addresses (e.g. `user@example.com`). Emails from any other address are rejected. |
| `GMAIL_PUBSUB_TOPIC` | Full Pub/Sub topic name (`projects/<project>/topics/<topic>`) |
| `GCS_RAW_DOCUMENTS_BUCKET` | GCS bucket name for raw PDF uploads |
| `GCS_ECO_OUTPUTS_BUCKET` | GCS bucket name for generated ECO PDF outputs |
| `SERVICE_URL` | Each service's own Cloud Run URL (used as OIDC token audience) |

## Getting Started

### 1. Authenticate with ADC (impersonate service account)

```bash
gcloud auth application-default login \
  --impersonate-service-account=pcn-agent-sa@<your-gcp-project-id>.iam.gserviceaccount.com
```

### 2. Run services locally

**Ingestor:**
```bash
cd ingestor
pip install -r requirements.txt
set -a && source ../.env && set +a
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
  -d '{"message": {"data": "<base64-encoded-json>", "messageId": "1", "publishTime": "2026-01-01T00:00:00Z"}, "subscription": "projects/<project>/subscriptions/gmail-pcn-sub"}'
```

---

## Infrastructure Provisioning Guide

The pipeline relies on pre-provisioned GCP infrastructure. If you are setting this up from scratch, run the following commands to provision the necessary services, storage, and IAM roles.

> [!NOTE]
> Ensure you have authenticated with `gcloud auth login` before running these commands.

### 1. Enable APIs & Set Project

```bash
gcloud services enable run.googleapis.com eventarc.googleapis.com \
pubsub.googleapis.com storage.googleapis.com \
firestore.googleapis.com aiplatform.googleapis.com

gcloud config set project <your-gcp-project-id>
```

### 2. Provision Storage & Database

> [!TIP]
> A 7-day lifecycle policy is applied to buckets to automatically clean up raw PCNs and generated ECOs, managing storage costs effectively.

```bash
# Create GCS Buckets for document storage & outputs
gcloud storage buckets create gs://pcn-raw-documents --location=asia-south1
gcloud storage buckets create gs://eco-outputs --location=asia-south1

# Initialize Firestore Database in Native Mode
gcloud firestore databases create --location=asia-south1 --type=firestore-native

# Create and apply a local lifecycle policy file
echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":7}}]}' > lifecycle.json
gcloud storage buckets update gs://pcn-raw-documents --lifecycle-file=lifecycle.json
gcloud storage buckets update gs://eco-outputs --lifecycle-file=lifecycle.json
rm lifecycle.json
```

### 3. Configure Eventing & Service Accounts

> [!IMPORTANT]
> The Pub/Sub subscription is configured to drop or redirect failed events after 3 retries. This prevents poison-pill messages from infinite looping and consuming resources.

```bash
# Update subscription to drop or redirect failed events after 3 retries
gcloud pubsub subscriptions update <your-pubsub-subscription-name> \
  --max-delivery-attempts=3

# Create dedicated service account
gcloud iam service-accounts create pcn-agent-sa \
  --display-name="PCN Agent Service Account"

# Grant only required roles
gcloud projects add-iam-policy-binding <your-gcp-project-id> \
  --member="serviceAccount:pcn-agent-sa@<your-gcp-project-id>.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

gcloud projects add-iam-policy-binding <your-gcp-project-id> \
  --member="serviceAccount:pcn-agent-sa@<your-gcp-project-id>.iam.gserviceaccount.com" \
  --role="roles/datastore.user"
```

### 4. Secrets & Eventarc Trigger

> [!CAUTION]
> Never hardcode your GitHub PAT. We use Secret Manager here to store the token securely. Note that our final deployment passes this via Cloud Run environment variables for simplicity in this guide, but Secret Manager is the recommended production approach.

```bash
# Store GitHub PAT securely
gcloud secrets create GITHUB_TOKEN --replication-policy="automatic"
echo -n "<your_github_pat_here>" | gcloud secrets versions add GITHUB_TOKEN --data-file=-

# Deploy Agent Service to Cloud Run (initial deployment for Eventarc)
# Note: The final pipeline strictly requires --no-allow-unauthenticated for security.
gcloud run deploy pcn-agent \
  --source . \
  --region asia-south1 \
  --platform managed \
  --no-allow-unauthenticated \
  --min-instances=0 \
  --max-instances=2 \
  --concurrency=10 \
  --timeout=300s \
  --memory 1Gi \
  --cpu 1

# Bind GCS Object Creation to Eventarc -> Cloud Run
gcloud eventarc triggers create pcn-gcs-trigger \
  --location=asia-south1 \
  --destination-run-service=pcn-agent \
  --destination-run-region=asia-south1 \
  --event-filters="type=google.cloud.storage.object.v1.finalized" \
  --event-filters="bucket=pcn-raw-documents" \
  --service-account="$(gcloud projects describe <your-gcp-project-id> --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
```

---

## GCP Deployment

Deploy to Cloud Run via `gcloud` (ensure `--no-allow-unauthenticated` is applied).

```bash
# Deploy ingestor
gcloud run deploy pcn-ingestor \
  --source ./ingestor \
  --region asia-south1 \
  --no-allow-unauthenticated \
  --project <your-gcp-project-id> \
  --set-env-vars="GCS_RAW_DOCUMENTS_BUCKET=pcn-raw-documents,GMAIL_CLIENT_ID=...,GMAIL_CLIENT_SECRET=...,GMAIL_REFRESH_TOKEN=...,SERVICE_URL=<ingestor-url>,ALLOWED_SENDERS=..."

# Deploy agent
gcloud run deploy pcn-agent \
  --source ./agent \
  --region asia-south1 \
  --no-allow-unauthenticated \
  --project <your-gcp-project-id> \
  --set-env-vars="GCP_PROJECT_ID=<your-gcp-project-id>,GCP_REGION=asia-south1,GCS_RAW_DOCUMENTS_BUCKET=pcn-raw-documents,GCS_ECO_OUTPUTS_BUCKET=eco-outputs,GITHUB_TOKEN=...,GITHUB_TARGET_REPO=...,SERVICE_URL=<agent-url>"
```

---

## Operational Maintenance

### Gmail Watch Renewal

The Gmail API `users.watch()` registration expires every **~7 days**. Until the automated Cloud Scheduler job is deployed, you must manually run the renewal script each week:

```bash
cd scripts
pip install google-auth google-api-python-client
set -a && source ../.env && set +a
python gmail_watch_renew.py
```

**Known limitation:** If the watch expires, the ingestor will silently stop receiving new email notifications.

**Planned Cloud Scheduler Setup:**
```bash
# Create a Cloud Scheduler job to renew weekly (after packaging as a Cloud Run Job)
gcloud scheduler jobs create http gmail-watch-renew \
  --schedule="0 0 * * 0" \
  --uri="<cloud-run-job-url>" \
  --oidc-service-account-email=pcn-agent-sa@<your-gcp-project-id>.iam.gserviceaccount.com \
  --location=asia-south1
```

---

## Project Folder Structure

```text
pcn-orchestrator-2026/
├── ingestor/                     # Gmail push receiver → GCS upload service
│   ├── main.py                   # FastAPI server handling Pub/Sub Push payload
│   ├── requirements.txt
│   └── Dockerfile
├── agent/                        # Eventarc handler + ADK multi-agent triage pipeline
│   ├── main.py                   # FastAPI server handling Eventarc trigger and Agent orchestration
│   ├── tools.py                  # Agent capability tools (Firestore query, GitHub PR, PDF Gen)
│   ├── requirements.txt
│   └── Dockerfile
├── docs/
│   └── diagrams/                 # Mermaid architecture definitions and rendered PNGs
├── scripts/
│   ├── gmail_oauth_setup.py      # One-time OAuth setup
│   └── gmail_watch_renew.py      # Weekly renewal script
├── test_pdfs/                    # Example PCN PDFs used for testing
├── secrets/                      # gitignored, holds OAuth client JSON
├── .github/workflows/
│   └── ci-validation.yml         # CI smoke tests and linting
├── .env.example
├── AGENTS.md                     # Single source of truth for architectural requirements
└── README.md
```

---

## Architecture & Tech Stack Details

The system is designed with two distinct, decoupled microservices. The `pcn-ingestor` handles the intake of incoming Product Change Notification emails, while the `pcn-agent` handles the LLM triage and resolution tasks asynchronously.

### 1. Ingestion Sequence

The ingestion sequence translates raw email into structured GCS storage. 
1. **Gmail Push Notification**: A Gmail account configured with `users.watch()` publishes a `historyId` to a Pub/Sub topic when an email arrives.
2. **Pub/Sub Push**: Pub/Sub pushes the notification envelope to `pcn-ingestor`'s `POST /` endpoint. The request includes a secure OIDC Bearer Token verifying the identity.
3. **Fetching and Validation**: The ingestor validates the signature, queries the Gmail API using the `historyId` to retrieve the new message, and strictly checks the `From` header against the `ALLOWED_SENDERS` list.
4. **Extraction**: Any PDF attached to the verified email is downloaded and uploaded to the `pcn-raw-documents` GCS bucket.

<p align="center">
  <img src="docs/diagrams/ingestion-sequence.png?v=2" alt="Ingestion Sequence" width="650" />
  <br>
  <em>Figure 1: Ingestion sequence diagram detailing the automated intake of incoming emails via Gmail push notifications, payload extraction, and subsequent upload of raw PCN PDFs to Google Cloud Storage.</em>
</p>

### 2. Multi-Agent Pipeline Flow

Uploading a PDF to the GCS bucket triggers an Eventarc CloudEvent, which is routed to the `pcn-agent` service. To ensure resilience and prevent "infinite loops" and hallucinations, the orchestration abandons the built-in `SequentialAgent` in favor of a strictly managed 3-stage manual pipeline wrapped around distinct Google ADK 2.0 `Runner` invocations. 

State is passed forward deterministically: the structured JSON output of one stage is strictly parsed in code before being injected into the prompt of the subsequent stage.

1. **Stage 1 (Triage Agent)**: Reads the newly uploaded multi-page PDF natively via Gemini 3.5 Flash's multimodal capabilities. Extracts all affected component part numbers into a clean `{"parts": ["<part1>", ...]}` JSON structure.
2. **Stage 2 (Resolution Agent)**: Consumes the parts list, leverages the `query_firestore_inventory` tool to cross-reference each part against the `inventory` collection in Firestore, determining what parts are active and what replacements are available. Output structure: `{"parts": [{"part_number": ..., "found": bool, ...}]}`.
3. **Stage 3 (Action Agent)**: Consumes the resolved inventory data. Uses `github_create_pr` to create a new branch and Pull Request for affected HAL headers, and `generate_eco_pdf` to create an Engineering Change Order PDF uploaded to the `eco-outputs` GCS bucket. Finally, the run state is persisted to the `agent_runs` Firestore collection.

By executing stages via a manual `run_stage_with_retry` wrapper, the architecture guarantees that a failure in Stage 3 (e.g. a GitHub API timeout) only retries Stage 3, rather than redundantly invoking the expensive, multimodal Stage 1 PDF extraction again.

<p align="center">
  <img src="docs/diagrams/multi-agent-flow.png?v=2" alt="Multi-Agent Flow" width="650" />
  <br>
  <em>Figure 2: Flow chart of the 3-Stage Multi-Agent pipeline, demonstrating the resilient state passing via JSON parsing and strictly separated Google ADK Runner boundaries.</em>
</p>

### System Context

The overall system architecture displays how the orchestrator connects external triggers and APIs (Gmail, GitHub, Vertex AI) to internal managed state (Firestore, Cloud Storage).

<p align="center">
  <img src="docs/diagrams/system-architecture.png?v=2" alt="System Architecture" width="750" />
  <br>
  <em>Figure 3: High-level System Architecture illustrating the fully decoupled microservices, external APIs, and internal state persistence within Firestore.</em>
</p>

---

## Security & Cost Safety Nets

To guarantee secure execution and prevent run-away GCP bills, this pipeline implements multiple rigorous safety nets:

1. **Cost Guard (Payload Size Check):** The agent service strictly enforces a 5MB size limit on incoming GCS objects. Anything larger is immediately rejected (`413 Payload Too Large`), preventing excessive LLM token usage and context exhaustion.
2. **Sender Allowlist:** The ingestion service verifies the `From` header of incoming emails against a strict `ALLOWED_SENDERS` environment variable list. Unrecognized senders are rejected to prevent spam from triggering the agent pipeline.
3. **Locked-Down Ingress:** Both `pcn-ingestor` and `pcn-agent` Cloud Run services are deployed with `--no-allow-unauthenticated`. They rely on secure OIDC (OpenID Connect) bearer tokens issued by Eventarc and Pub/Sub respectively.
4. **IAM Keyless Auth:** The application strictly uses Application Default Credentials (ADC). No Service Account Key JSON files are ever generated or used, complying with `iam.disableServiceAccountKeyCreation` org policies.
5. **Exponential Backoff:** All calls to Vertex AI are wrapped in a 3-attempt exponential backoff retry block to safely tolerate transient `503` or rate-limiting errors.
6. **Isolated Stage Retries:** A custom `run_stage_with_retry` function ensures that if a downstream action (like creating a PR) fails, only that specific stage is retried. Successfully executed stages (like the expensive PDF extraction) are not redundantly invoked.
7. **Strict Model Constraints:** We lock the deployment to `gemini-3.5-flash` to optimize for speed and predictable cost.
