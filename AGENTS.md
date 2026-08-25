# Autonomous PCN Triage Orchestrator

## 0. Current State — Read This First

This is not a greenfield build. The following infrastructure is **already provisioned and live**
in GCP project `pcn-orchestrator-2026` (region `asia-south1`). Do not attempt to recreate any of it.
Your job is to write/replace the application code inside `ingestor/` and `agent/` to match this spec.

Already live:
- GCS buckets: `pcn-raw-documents`, `eco-outputs`
- Firestore (Native mode) database, initialized
- Service account `pcn-agent-sa@pcn-orchestrator-2026.iam.gserviceaccount.com` with roles:
  Storage Admin, Cloud Datastore User, Eventarc Event Receiver, Vertex AI User
- Cloud Run service `pcn-agent` (asia-south1) — deployed from a stub, **locked down**
  (`roles/run.invoker` granted only to `pcn-agent-sa`, not public)
- Cloud Run service `pcn-ingestor` (asia-south1) — deployed from a stub, **locked down**
  (`roles/run.invoker` granted only to `pcn-agent-sa`, not public)
- Eventarc trigger `pcn-gcs-trigger`: fires on `google.cloud.storage.object.v1.finalized` for
  bucket `pcn-raw-documents`, targets `pcn-agent`, runs as `pcn-agent-sa`
- Pub/Sub topic `gmail-pcn-notifications`, with `gmail-api-push@system.gserviceaccount.com`
  granted `roles/pubsub.publisher`
- Pub/Sub push subscription `gmail-pcn-sub`: topic `gmail-pcn-notifications`, push endpoint
  is the `pcn-ingestor` URL, push auth service account is `pcn-agent-sa`
- Gmail API `users.watch()` already registered for mailbox `shawngdg2005@gmail.com`, pushing
  to `gmail-pcn-notifications` (expires every ~7 days — see Section 6, renewal is TODO)
- Local dev auth is via ADC impersonation (`gcloud auth application-default login
  --impersonate-service-account=pcn-agent-sa@pcn-orchestrator-2026.iam.gserviceaccount.com`).
  **No service account key files exist or can be created** — an org policy
  (`iam.disableServiceAccountKeyCreation`) blocks key creation. Do not write code that expects
  a key file, and never call `GOOGLE_APPLICATION_CREDENTIALS` with a JSON key path.

Email ingestion does **not** use SendGrid. There is no owned domain available for MX records.
Ingestion is Gmail API + Pub/Sub push (see Section 3).

---

## 1. Tech Stack

| Component | Selection |
| :--- | :--- |
| Agent Framework | Google ADK 2.0 (Python) |
| Model Runtime | `gemini-3.5-flash` via Vertex AI (region `asia-south1`) — **not** `-latest`, that suffix does not exist for Gemini model IDs |
| Ingestion | Gmail API `users.watch()` → Pub/Sub push → Cloud Run (`pcn-ingestor`) |
| Event Routing | Google Cloud Eventarc (GCS finalize → `pcn-agent`) |
| Memory / State | Firestore (Native mode) |
| Web Server | FastAPI, both services |

---

## 2. Repository Layout

```text
pcn-orchestrator-2026/
├── ingestor/
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── agent/
│   ├── main.py
│   ├── tools.py
│   ├── requirements.txt
│   └── Dockerfile
├── scripts/
│   ├── gmail_oauth_setup.py      # one-time local OAuth authorization, already run once
│   └── gmail_watch_renew.py      # to be built — see Section 6
├── secrets/                      # gitignored, holds OAuth client JSON, never committed
├── .github/workflows/
│   └── ci-validation.yml
├── architecture.png
├── .env.example
├── .env                          # gitignored, real local values, never committed
├── .gitignore
└── README.md
```

`ingestor/` and `agent/` currently contain placeholder stub code (a bare `POST /` returning
`{"status": "ok"}`) that was deployed only to reserve the Cloud Run URLs and validate the
Pub/Sub and Eventarc wiring end to end. **Replace both stubs entirely** with the real
implementation below.

---

## 3. `ingestor/main.py` — Gmail Push Receiver

Implements a FastAPI server receiving Pub/Sub push messages (not raw email — Gmail API events).

1. **Endpoint:** `POST /`, accepts Pub/Sub's push JSON envelope:
   ```json
   { "message": { "data": "<base64>", "messageId": "...", "publishTime": "..." }, "subscription": "..." }
   ```
2. **Auth verification:** Pub/Sub push includes a signed OIDC identity token in the
   `Authorization: Bearer <token>` header, issued for the service account configured on the
   subscription (`pcn-agent-sa`). Verify this token (audience = the service's own URL, issuer =
   Google) before processing. Reject with `401` if invalid or missing. Do not implement any
   other authentication scheme here — Pub/Sub push auth replaces the SendGrid-style signature
   check entirely.
3. **Decode payload:** `message.data` is base64 JSON: `{"emailAddress": "...", "historyId": "..."}`.
   This tells you *something changed*, not what — you must fetch the actual message.
4. **Fetch new message via Gmail API:**
   - Maintain the last-processed `historyId` in Firestore (collection `gmail_sync_state`,
     single document, field `last_history_id`). On first run, seed it from Firestore or fall
     back to the `historyId` in the current push payload.
   - Call `users.history.list(userId="me", startHistoryId=<last known id>)` to find new
     messages added to `INBOX` since then.
   - For each new message: call `users.messages.get(userId="me", id=<msg id>)` to retrieve it.
   - Update the stored `last_history_id` in Firestore after successful processing.
5. **Extract PDF attachment:** walk the message payload parts for `mimeType ==
   "application/pdf"`, fetch via `users.messages.attachments.get`, decode from base64url.
6. **Upload to GCS:** upload the decoded PDF bytes to bucket `pcn-raw-documents`, object name
   `<gmail-message-id>.pdf`. This upload is what fires the existing Eventarc trigger — do not
   call the agent directly from here, the two services stay decoupled exactly as before.
7. **Auth for outbound calls (Gmail API, Firestore, GCS):** Gmail API calls use OAuth (refresh
   token from env vars — see Section 5), Firestore/GCS calls use ADC (automatic on Cloud Run via
   attached `pcn-agent-sa`, and via impersonation locally — no code difference needed for either).
8. **Response:** always return `200 OK` promptly (Pub/Sub retries on non-2xx, and slow acks
   cause backlog) — do the GCS upload synchronously before returning, this ingestion path is
   not expected to be slow enough to need backgrounding for a hackathon-scale demo.

---

## 4. `agent/tools.py` — Action Layer

Three tool functions:

1. **`query_firestore_inventory(part_number: str) -> dict`**
   Queries Firestore collection `inventory`. Exact document schema:
   ```json
   { "part_number": "str", "replacement_part_numbers": ["str"], "status": "str", "datasheet_uri": "str" }
   ```
2. **`github_create_pr(repo_url: str, branch: str, hal_modifications: dict) -> dict`**
   Use `PyGithub`. Auth via `GITHUB_TOKEN` env var (fine-grained PAT, scoped to
   `Contents: write` + `Pull requests: write` on the target repo only). Clone/checkout the
   target repo, update HAL header file(s) per `hal_modifications`, commit to a new branch,
   open a PR against `main`.
3. **`generate_eco_pdf(report_data: str) -> dict`**
   Generate a PDF (use `reportlab` or `fpdf2`) and upload to bucket `eco-outputs`, object name
   `ECO-<timestamp>.pdf`.

---

## 5. `agent/main.py` — Eventarc Handler + ADK Agent

1. **Agent Initialization:**
   ```python
   agent = Agent(
       name="pcn_triage_agent",
       model="gemini-3.5-flash",
       instruction="<strict autonomous triage instructions, no user intervention>",
       tools=[query_firestore_inventory, github_create_pr, generate_eco_pdf],
   )
   ```
   Initialize the Vertex AI client with `project=GCP_PROJECT_ID`, `location=GCP_REGION`
   (both from env vars — see Section 7). Do not hardcode the project or region.

2. **Endpoint:** `POST /`, receives Eventarc CloudEvent. Read `ce-subject` header to get the
   GCS object name. Also verify the Eventarc-issued OIDC token the same way as described in
   Section 3 step 2 — same mechanism, this service is equally locked down.

3. **Cost Guard:** before invoking the agent, check the uploaded PDF's size via the GCS SDK.
   If it exceeds 5MB, log a `"Payload Too Large"` entry to Firestore collection `agent_runs`
   with `status: "REJECTED_SIZE"` and return `200 OK` without invoking the LLM.

4. **Execution:** construct `gs://pcn-raw-documents/<object_name>`, prompt the agent to
   triage against target repo `GITHUB_TARGET_REPO` (env var).

5. **State Persistence:** write to Firestore collection `agent_runs`:
   ```json
   { "gcs_uri": "str", "target_repo": "str", "response": "str", "status": "str", "timestamp": "<server timestamp>" }
   ```

6. Return `200 OK` with a JSON summary.

---

## 6. Gmail Watch Renewal (Cloud Scheduler)

`users.watch()` expires every ~7 days. Build `scripts/gmail_watch_renew.py` as a small
standalone script (same logic as the existing `scripts/gmail_oauth_setup.py`/`gmail_watch.py`
reference, using the stored refresh token — no interactive login needed for renewal calls).
Package it as a lightweight Cloud Run job or Cloud Function, triggered weekly by Cloud
Scheduler. This is lower priority than the core pipeline — implement after Sections 3–5 are
working, but before final submission if time allows, otherwise document it as a known
limitation in the README.

---

## 7. Environment Variables

All of the following must be read from environment variables — **never hardcode any value
below in source code**. A fully populated `.env` already exists locally (gitignored, not part
of this repo) with real values. Your job is to write code that reads these names; you do not
need the actual values to write correct code.

```
GCP_PROJECT_ID
GCP_REGION
GITHUB_TOKEN
GITHUB_TARGET_REPO
GMAIL_CLIENT_ID
GMAIL_CLIENT_SECRET
GMAIL_REFRESH_TOKEN
GMAIL_WATCHED_ADDRESS
GMAIL_PUBSUB_TOPIC
GCS_RAW_DOCUMENTS_BUCKET
GCS_ECO_OUTPUTS_BUCKET
SERVICE_URL
```

Do not set or expect `GOOGLE_APPLICATION_CREDENTIALS` — Firestore/GCS/Vertex AI auth is via
ADC (service-account impersonation locally, attached service account identity on Cloud Run).
Write `.env.example` listing these same variable names with empty/placeholder values (no real
secrets) so anyone cloning the repo knows what to fill in.

---

## 8. Dockerfiles

Both `ingestor/Dockerfile` and `agent/Dockerfile`:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

---

## 9. CI/CD

`.github/workflows/ci-validation.yml`: trigger on `push` and `pull_request`. Install
dependencies for both `ingestor/` and `agent/`, run `flake8` on both, and run a basic import
smoke test (`python -c "import main"` in each directory) as a build validation step. No
deployment step in CI — deployment stays manual via `gcloud run deploy` for this hackathon.

---

## 10. README.md (Required Hackathon Deliverable)

Must include, as explicit step-by-step instructions:
1. Prerequisites (gcloud CLI, Python 3.11+, a GCP project with the infra from Section 0).
2. Local `.env` setup — list the variable names from Section 7, explain each briefly.
3. Local run instructions (`uvicorn main:app --reload` per service, plus the ADC impersonation
   login command from Section 0).
4. GCP deployment commands (`gcloud run deploy` for both services, matching Section 8's
   Dockerfiles).
5. A short note that Gmail watch expires weekly and must be renewed (Section 6), until the
   Cloud Scheduler job is in place.

---

## 11. Deployment Commands (Reference — Already Executed Once for Stubs)

```
gcloud run deploy pcn-ingestor --source ./ingestor --region asia-south1 --no-allow-unauthenticated --project pcn-orchestrator-2026
gcloud run deploy pcn-agent --source ./agent --region asia-south1 --no-allow-unauthenticated --project pcn-orchestrator-2026
```

Both services keep `--no-allow-unauthenticated`. IAM bindings granting `pcn-agent-sa` the
`roles/run.invoker` role on both services are already in place and do not need to be redone
unless a service is deleted and recreated.
