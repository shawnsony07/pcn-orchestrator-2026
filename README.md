# PCN Triage Orchestrator

An autonomous PCN (Product Change Notification) triage pipeline using Google ADK 2.0, Gemini 3.5 Flash, Eventarc, Gmail API, Pub/Sub, Firestore, and Cloud Run.

## Overview

The PCN Triage Orchestrator automates the intake and resolution of hardware Product Change Notifications. It watches a Gmail inbox for incoming PCN emails, reads attached PDFs natively via Gemini's multimodal capabilities (including scanned, image-only documents with no extractable text layer), cross-references affected parts against a Firestore inventory, and autonomously opens GitHub Pull Requests updating firmware HAL headers plus generates Engineering Change Order (ECO) PDFs — with no human in the loop between email arrival and PR creation.

Built for the **All Things Agentic Hackathon**, Taskmaster track.

---

## Project Structure

```text
pcn-orchestrator-2026/
├── ingestor/                     # Gmail push receiver → GCS upload service
│   ├── main.py                   # FastAPI server: Pub/Sub push, sender allowlist, Gmail fetch, GCS upload
│   ├── requirements.txt
│   └── Dockerfile
├── agent/                        # Eventarc handler + multi-agent triage pipeline
│   ├── main.py                   # FastAPI server: Eventarc trigger, 3-stage agent orchestration
│   ├── tools.py                  # Firestore inventory query, GitHub PR creation, ECO PDF generation
│   ├── requirements.txt
│   └── Dockerfile
├── scripts/
│   ├── gmail_oauth_setup.py      # One-time interactive OAuth authorization (run once, locally)
│   ├── gmail_watch_renew.py      # Non-interactive weekly watch() renewal (uses stored refresh token)
│   └── seed_inventory.py         # Seeds the Firestore `inventory` collection with test parts
├── test_pdfs/                    # Sample PCN PDFs for manual and regression testing
├── docs/diagrams/                # Mermaid architecture diagram sources + rendered PNGs
├── secrets/                      # gitignored — holds the Gmail OAuth client JSON, never committed
├── .github/workflows/
│   └── ci-validation.yml         # flake8 + import smoke tests on push/PR
├── .env.example
├── .gitignore
├── AGENTS.md                     # Full build specification (source of truth for architecture)
└── README.md
```

---

## Local Development Prerequisites

1. **gcloud CLI** — [Install](https://cloud.google.com/sdk/docs/install), then authenticate:
   ```bash
   gcloud auth login
   gcloud config set project <your-gcp-project-id>
   ```
2. **Python 3.11+**
3. **A GCP project** with the infrastructure described in the Provisioning Guide below.

## Environment Variables (.env)

Copy `.env.example` to `.env` and fill in real values. **Never commit `.env`.**

| Variable | Description |
|---|---|
| `GCP_PROJECT_ID` | Your GCP project ID |
| `GCP_REGION` | GCP region where services are deployed (e.g. `asia-south1`) |
| `GITHUB_TOKEN` | Fine-grained PAT with `Contents: write` + `Pull requests: write` on the target repo only |
| `GITHUB_TARGET_REPO` | Target repo for HAL updates (`owner/repo` or full HTTPS URL) |
| `GMAIL_CLIENT_ID` | OAuth 2.0 client ID from GCP Console |
| `GMAIL_CLIENT_SECRET` | OAuth 2.0 client secret |
| `GMAIL_REFRESH_TOKEN` | Refresh token obtained by running `scripts/gmail_oauth_setup.py` |
| `GMAIL_WATCHED_ADDRESS` | Gmail address being watched for PCN emails |
| `ALLOWED_SENDERS` | Comma-separated list of authorized sender addresses. Emails from any other address are rejected before any processing. |
| `GMAIL_PUBSUB_TOPIC` | Full Pub/Sub topic name (`projects/<project>/topics/<topic>`) |
| `GCS_RAW_DOCUMENTS_BUCKET` | GCS bucket name for raw PDF uploads |
| `GCS_ECO_OUTPUTS_BUCKET` | GCS bucket name for generated ECO PDF outputs |
| `SERVICE_URL` | Each service's own Cloud Run URL — used as the OIDC token audience. Set separately per service, not shared. |

---

## Infrastructure Provisioning Guide (From Scratch)

This provisions everything needed for a fresh deployment. Run in order — later steps depend
on earlier ones. Skip this entirely if the infrastructure already exists (see AGENTS.md
Section 0 for what's already live in `pcn-orchestrator-2026`).

> Authenticate first: `gcloud auth login`

### 1. Enable APIs & Set Project

```bash
gcloud config set project <your-gcp-project-id>

gcloud services enable \
  run.googleapis.com \
  eventarc.googleapis.com \
  pubsub.googleapis.com \
  storage.googleapis.com \
  firestore.googleapis.com \
  aiplatform.googleapis.com \
  gmail.googleapis.com \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com
```

`iamcredentials.googleapis.com` is required for local service-account impersonation (step 6).
`cloudresourcemanager.googleapis.com` is required by some `gcloud run services replace`-style
commands used during development.

### 2. Provision Storage & Database

> A 7-day lifecycle policy is applied to both buckets to auto-delete raw PCNs and generated
> ECOs, keeping storage costs near zero.

```bash
gcloud storage buckets create gs://pcn-raw-documents --location=asia-south1
gcloud storage buckets create gs://eco-outputs --location=asia-south1

gcloud firestore databases create --location=asia-south1 --type=firestore-native

echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":7}}]}' > lifecycle.json
gcloud storage buckets update gs://pcn-raw-documents --lifecycle-file=lifecycle.json
gcloud storage buckets update gs://eco-outputs --lifecycle-file=lifecycle.json
rm lifecycle.json
```

### 3. Service Account

```bash
gcloud iam service-accounts create pcn-agent-sa \
  --display-name="PCN Agent Service Account"

gcloud projects add-iam-policy-binding <your-gcp-project-id> \
  --member="serviceAccount:pcn-agent-sa@<your-gcp-project-id>.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

gcloud projects add-iam-policy-binding <your-gcp-project-id> \
  --member="serviceAccount:pcn-agent-sa@<your-gcp-project-id>.iam.gserviceaccount.com" \
  --role="roles/datastore.user"

gcloud projects add-iam-policy-binding <your-gcp-project-id> \
  --member="serviceAccount:pcn-agent-sa@<your-gcp-project-id>.iam.gserviceaccount.com" \
  --role="roles/storage.admin"
```

> **No service account key files are created at any point.** This project relies entirely on
> Application Default Credentials — org policies commonly block key creation
> (`iam.disableServiceAccountKeyCreation`), and ADC avoids the risk of a leaked key file
> entirely. See step 6 for local development auth.

### 4. Deploy Stub Services (to reserve Cloud Run URLs for Eventarc/Pub-Sub targets)

Deploy minimal placeholder services first — real code replaces these in a later step, but
Eventarc and the Pub/Sub subscription need real URLs to target.

```bash
gcloud run deploy pcn-ingestor \
  --source ./ingestor --region asia-south1 --no-allow-unauthenticated \
  --project <your-gcp-project-id>

gcloud run deploy pcn-agent \
  --source ./agent --region asia-south1 --no-allow-unauthenticated \
  --project <your-gcp-project-id>
```

Grant `pcn-agent-sa` permission to invoke both services — without this, the locked-down
services (`--no-allow-unauthenticated`) reject every request, including from Eventarc and
Pub/Sub:

```bash
gcloud run services add-iam-policy-binding pcn-ingestor \
  --member="serviceAccount:pcn-agent-sa@<your-gcp-project-id>.iam.gserviceaccount.com" \
  --role="roles/run.invoker" --region=asia-south1

gcloud run services add-iam-policy-binding pcn-agent \
  --member="serviceAccount:pcn-agent-sa@<your-gcp-project-id>.iam.gserviceaccount.com" \
  --role="roles/run.invoker" --region=asia-south1
```

Fetch each service's canonical URL (needed for `SERVICE_URL` and the steps below):

```bash
gcloud run services describe pcn-ingestor --region=asia-south1 --format="value(status.url)"
gcloud run services describe pcn-agent --region=asia-south1 --format="value(status.url)"
```

### 5. Eventarc Trigger (GCS → `pcn-agent`)

```bash
gcloud eventarc triggers create pcn-gcs-trigger \
  --location=asia-south1 \
  --destination-run-service=pcn-agent \
  --destination-run-region=asia-south1 \
  --event-filters="type=google.cloud.storage.object.v1.finalized" \
  --event-filters="bucket=pcn-raw-documents" \
  --service-account="pcn-agent-sa@<your-gcp-project-id>.iam.gserviceaccount.com"
```

> The trigger's service account must be `pcn-agent-sa` — it already has `run.invoker` on
> `pcn-agent` from step 4. Using the default compute service account here will fail silently
> against a locked-down Cloud Run service.

### 6. Gmail API + Pub/Sub Ingestion Setup

This is the most involved part — Gmail push notifications require an OAuth-authorized
identity, not a service account, so there's a one-time manual authorization step.

**6a. OAuth consent screen + client:**
In GCP Console → APIs & Services → OAuth consent screen: set User Type to **External**, fill
in app name and support email, leave in **Testing** mode (no verification needed), and add
the Gmail address you intend to watch as a **test user**.

Then, APIs & Services → Credentials → Create Credentials → OAuth client ID → type **Desktop
app**. Download the resulting JSON into `secrets/`.

**6b. One-time local authorization:**
```bash
pip install google-auth-oauthlib google-auth google-api-python-client
python scripts/gmail_oauth_setup.py secrets/<downloaded_client_secret>.json
```
Log in as the Gmail address to watch when the browser opens. This prints
`GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` / `GMAIL_REFRESH_TOKEN` — save all three into `.env`.

**6c. Pub/Sub topic and permissions:**
```bash
gcloud pubsub topics create gmail-pcn-notifications --project=<your-gcp-project-id>

gcloud pubsub topics add-iam-policy-binding gmail-pcn-notifications \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
  --role="roles/pubsub.publisher" --project=<your-gcp-project-id>

gcloud pubsub subscriptions create gmail-pcn-sub \
  --topic=gmail-pcn-notifications \
  --push-endpoint=<pcn-ingestor URL from step 4>/ \
  --push-auth-service-account="pcn-agent-sa@<your-gcp-project-id>.iam.gserviceaccount.com" \
  --project=<your-gcp-project-id>
```

**6d. Register the watch:**
```bash
export GMAIL_CLIENT_ID=... GMAIL_CLIENT_SECRET=... GMAIL_REFRESH_TOKEN=... GCP_PROJECT_ID=<your-gcp-project-id>
python scripts/gmail_watch_renew.py
```
This expires in ~7 days and must be re-run periodically — see Operational Maintenance below.

### 7. GitHub Access

Create a fine-grained Personal Access Token scoped to the target repo only, with
`Contents: write` and `Pull requests: write` — nothing broader. Add it to `.env` as
`GITHUB_TOKEN`.

### 8. Seed Inventory (for testing)

```bash
export GCP_PROJECT_ID=<your-gcp-project-id>
python scripts/seed_inventory.py
```

### 9. Deploy Real Services

```bash
gcloud run deploy pcn-ingestor --source ./ingestor --region asia-south1 --no-allow-unauthenticated \
  --project <your-gcp-project-id> \
  --set-env-vars="GCS_RAW_DOCUMENTS_BUCKET=pcn-raw-documents,GMAIL_CLIENT_ID=...,GMAIL_CLIENT_SECRET=...,GMAIL_REFRESH_TOKEN=...,GMAIL_WATCHED_ADDRESS=...,ALLOWED_SENDERS=...,SERVICE_URL=<pcn-ingestor URL>"

gcloud run deploy pcn-agent --source ./agent --region asia-south1 --no-allow-unauthenticated \
  --project <your-gcp-project-id> \
  --set-env-vars="GCP_PROJECT_ID=<your-gcp-project-id>,GCP_REGION=asia-south1,GCS_RAW_DOCUMENTS_BUCKET=pcn-raw-documents,GCS_ECO_OUTPUTS_BUCKET=eco-outputs,GITHUB_TOKEN=...,GITHUB_TARGET_REPO=...,SERVICE_URL=<pcn-agent URL>"
```

> **Re-check `SERVICE_URL` after every `--source` redeploy.** Cloud Run occasionally reports
> a different canonical URL format for the same service across deploys. A stale
> `SERVICE_URL` causes OIDC audience-mismatch `401` errors on both the ingestor's Pub/Sub
> push endpoint and the agent's Eventarc endpoint — not an obvious deploy failure, since the
> deploy itself succeeds. Verify with `gcloud run services describe <service> --format="value(status.url)"`
> and update the env var if it changed.

---

## Local Development

**Authenticate via ADC impersonation** (no key files, ever):
```bash
gcloud auth application-default login \
  --impersonate-service-account=pcn-agent-sa@<your-gcp-project-id>.iam.gserviceaccount.com
```
Requires `roles/iam.serviceAccountTokenCreator` granted to your own user account on
`pcn-agent-sa` (project owners/creators typically already have this implicitly).

**Run services locally:**
```bash
# Ingestor
cd ingestor && pip install -r requirements.txt
set -a && source ../.env && set +a
uvicorn main:app --reload --port 8081

# Agent (separate terminal)
cd agent && pip install -r requirements.txt
set -a && source ../.env && set +a
uvicorn main:app --reload --port 8082
```

---

## Testing / How to Verify It Works

The most reliable end-to-end test mirrors exactly what happens in production:

1. **Send a real test email.** Attach one of the PDFs in `test_pdfs/` to an email sent from
   an address listed in `ALLOWED_SENDERS`, addressed to the watched Gmail address
   (`GMAIL_WATCHED_ADDRESS`).
2. **Watch it flow through, live, via Cloud Run logs:**
   ```bash
   gcloud run services logs read pcn-ingestor --region=asia-south1 --project=<your-gcp-project-id> --limit=30
   gcloud run services logs read pcn-agent --region=asia-south1 --project=<your-gcp-project-id> --limit=30
   ```
   Expect: ingestor logs show the push received, sender check passed, PDF uploaded to GCS.
   Agent logs show `[TRIAGE]` → `[RESOLUTION]` → `[ACTION]` in sequence.
3. **Check Firestore** — `agent_runs` collection should have a new document with a populated
   `extracted_parts` list.
4. **Check GitHub** — a new PR should appear on the target repo, branch named
   `pcn/<part_number>-<hash>`.
5. **Check GCS `eco-outputs`** — a new `ECO-<timestamp>.pdf` should be present.

`test_pdfs/` includes cases exercising different code paths: a text-based single-part PDF, a
genuinely scanned (zero-text-layer) single-part PDF, a single-part PDF for a part
deliberately not in the seeded inventory (expect `NO_INVENTORY_MATCH`, no PR), and a 3-page
multi-part PDF combining a found and an unfound part in one document.

To test the ingestor's Pub/Sub handling directly without sending real email:
```bash
curl -X POST http://localhost:8081/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <valid-oidc-token>" \
  -d '{"message": {"data": "<base64-encoded-json>", "messageId": "1", "publishTime": "2026-01-01T00:00:00Z"}, "subscription": "projects/<project>/subscriptions/gmail-pcn-sub"}'
```

---

## Operational Maintenance

### Gmail Watch Renewal

`users.watch()` expires every **~7 days**. Until an automated Cloud Scheduler job is deployed
(see Known Limitations), renew manually:

```bash
cd scripts
pip install google-auth google-api-python-client
set -a && source ../.env && set +a
python gmail_watch_renew.py
```

**Planned Cloud Scheduler setup** (package `gmail_watch_renew.py` as a Cloud Run Job first):
```bash
gcloud scheduler jobs create http gmail-watch-renew \
  --schedule="0 0 * * 0" \
  --uri="<cloud-run-job-url>" \
  --oidc-service-account-email=pcn-agent-sa@<your-gcp-project-id>.iam.gserviceaccount.com \
  --location=asia-south1
```

---

## Architecture & Tech Stack Details

Two decoupled microservices. `pcn-ingestor` handles email intake; `pcn-agent` handles LLM
triage and resolution, asynchronously, via a 3-stage agent pipeline.

### 1. Ingestion Sequence

1. **Gmail Push Notification** — a Gmail account with `users.watch()` registered publishes a
   `historyId` to a Pub/Sub topic when new mail arrives.
2. **Pub/Sub Push** — Pub/Sub pushes the notification envelope to `pcn-ingestor`'s `POST /`
   endpoint, with a signed OIDC bearer token proving the request's identity.
3. **Fetching and Validation** — the ingestor verifies the token, queries the Gmail API using
   the `historyId` to retrieve the new message, and checks the `From` header against
   `ALLOWED_SENDERS` before doing anything else with the message.
4. **Extraction** — any PDF attached to a verified, allowed email is downloaded and uploaded
   to the `pcn-raw-documents` GCS bucket.

<p align="center">
  <img src="docs/diagrams/ingestion-sequence.png" alt="Ingestion Sequence" width="650" />
  <br>
  <em>Figure 1: Ingestion sequence — Gmail push notification through to raw PCN PDF landing in Cloud Storage.</em>
</p>

### 2. Multi-Agent Pipeline Flow

Uploading a PDF to GCS fires an Eventarc CloudEvent, routed to `pcn-agent`. The pipeline is
a manually orchestrated 3-stage sequence — three independent ADK `Agent` + `Runner` pairs
sharing one session, rather than ADK's built-in `SequentialAgent`, which was tried and
abandoned after empirical testing showed it cannot resume mid-sequence after a failure
without a resumable-session backend. Manual orchestration guarantees a failure in a later
stage (e.g. a GitHub API timeout in Stage 3) retries only that stage — the expensive,
multimodal Stage 1 PDF read is never redundantly repeated.

State passes forward deterministically: each stage's JSON output is parsed in code — not
just re-embedded as raw text — before being handed to the next stage.

1. **Stage 1 (Triage)** — reads the uploaded PDF natively via Gemini 3.5 Flash's multimodal
   capability (handles multi-page documents and scanned, image-only PDFs equally). Extracts
   every distinct affected part number into `{"parts": [...]}`.
2. **Stage 2 (Resolution)** — cross-references each part against the Firestore `inventory`
   collection. Never fabricates a replacement for a part it can't find — reports `found:
   false` explicitly instead.
3. **Stage 3 (Action)** — for each resolved part, opens a GitHub PR updating the relevant HAL
   header (deterministic branch naming, idempotent — a re-triggered run reuses the existing
   branch rather than opening a duplicate PR) and generates an ECO PDF. Final state is
   persisted to the `agent_runs` Firestore collection, one entry per document, with
   per-part results.

<p align="center">
  <img src="docs/diagrams/multi-agent-flow.png" alt="Multi-Agent Flow" width="650" />
  <br>
  <em>Figure 2: The 3-stage manual pipeline — Triage → Resolution → Action — with isolated per-stage retries.</em>
</p>

### System Context

<p align="center">
  <img src="docs/diagrams/system-architecture.png" alt="System Architecture" width="750" />
  <br>
  <em>Figure 3: Full system — decoupled microservices, external APIs (Gmail, GitHub, Vertex AI), and Firestore/GCS state.</em>
</p>

---

## Security & Cost Safety Nets

1. **Sender Allowlist** — the ingestor checks the `From` header of every incoming email
   against `ALLOWED_SENDERS` before any processing. Unrecognized senders are rejected.
2. **Locked-Down Ingress** — both Cloud Run services run with `--no-allow-unauthenticated`,
   relying on OIDC bearer tokens issued by Eventarc and Pub/Sub respectively. Nothing else
   can invoke either service.
3. **Keyless IAM Auth** — Application Default Credentials only. No service account key JSON
   files are ever generated or used, consistent with `iam.disableServiceAccountKeyCreation`
   org policy.
4. **Cost Guard** — a 5MB size limit on incoming GCS objects, checked before any LLM call.
   Oversized or unreadable objects are rejected and logged, never processed.
5. **No Fabricated Replacements** — if a part isn't found in the Firestore inventory, the
   pipeline explicitly reports `NO_INVENTORY_MATCH` rather than inventing a plausible-looking
   replacement. This was a real failure mode caught during development (an early version of
   the agent, given only a bare GCS URI with no way to actually read the PDF, hallucinated a
   part number entirely) — the current architecture makes that class of error structurally
   harder by giving each stage a narrow, explicit contract.
6. **Deterministic, Idempotent PR Branching** — branch names are computed in code from the
   part number and a hash of the source document, never left to LLM discretion. A retriggered
   run for the same document reuses the existing branch instead of opening a duplicate PR.
7. **Exponential Backoff** — Vertex AI calls retry up to 3 times with exponential backoff for
   transient errors, isolated per pipeline stage so a failure doesn't force redundant re-runs
   of earlier, already-successful stages.
8. **Strict Model Pinning** — locked to `gemini-3.5-flash` specifically (not a `-latest`
   alias) for predictable behavior and cost.
9. **GCS Lifecycle Policy** — both buckets auto-delete objects after 7 days, bounding storage
   cost regardless of test volume.

---

## Known Limitations

- **Gmail watch requires manual renewal** every ~7 days until the Cloud Scheduler job
  described above is deployed. If it lapses, the ingestor stops receiving new-mail
  notifications silently — no error is raised, mail simply isn't picked up.
- **One commit per file in generated PRs** — `github_create_pr` uses the GitHub Contents API
  rather than a local git clone, so each modified file produces its own commit rather than
  one atomic multi-file commit. Acceptable for the current single-file HAL update case; would
  need a Git Data (Trees) API rewrite to support atomic multi-file commits.
- **Per-stage retry isolation was validated via a mocked test harness**, not by forcing a
  failure in the live, deployed Cloud Run pipeline. The pattern (same `session_id` reused,
  only the failed stage retried) was proven correct in isolation and the real deployed
  pipeline has run cleanly end-to-end multiple times, but the retry-under-failure path
  specifically has not been exercised against production infrastructure.
- **No automatic scaling test** — the pipeline has been validated on individual test
  documents, not under concurrent load.
