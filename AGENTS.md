# Autonomous PCN Triage Orchestrator

## 0. Current State — Read This First

This is not a greenfield build. The following infrastructure is **already provisioned and live**
in GCP project `pcn-orchestrator-2026` (region `asia-south1`). Do not attempt to recreate any of it.
Your job is to write/maintain the application code inside `ingestor/` and `agent/` to match this spec.

Already live:
- GCS buckets: `pcn-raw-documents`, `eco-outputs` (both with a 7-day object lifecycle delete policy)
- Firestore (Native mode) database, initialized
- Service account `pcn-agent-sa@pcn-orchestrator-2026.iam.gserviceaccount.com` with roles:
  Storage Admin, Cloud Datastore User, Eventarc Event Receiver, Vertex AI User
- Cloud Run services `pcn-agent` and `pcn-ingestor` (asia-south1), both **locked down**
  (`roles/run.invoker` granted only to `pcn-agent-sa`, not public)
- Eventarc trigger `pcn-gcs-trigger`: fires on `google.cloud.storage.object.v1.finalized` for
  bucket `pcn-raw-documents`, targets `pcn-agent`, runs as `pcn-agent-sa`
- Pub/Sub topic `gmail-pcn-notifications`, with `gmail-api-push@system.gserviceaccount.com`
  granted `roles/pubsub.publisher`
- Pub/Sub push subscription `gmail-pcn-sub`: topic `gmail-pcn-notifications`, push endpoint
  is the `pcn-ingestor` URL, push auth service account is `pcn-agent-sa`
- Gmail API `users.watch()` registered for mailbox `shawngdg2005@gmail.com`, pushing to
  `gmail-pcn-notifications`. Expires every ~7 days — renewal is manual via
  `scripts/gmail_watch_renew.py` until a Cloud Scheduler job is added (see Section 6).
- Local dev auth is via ADC impersonation (`gcloud auth application-default login
  --impersonate-service-account=pcn-agent-sa@pcn-orchestrator-2026.iam.gserviceaccount.com`).
  **No service account key files exist or can be created** — org policy
  `iam.disableServiceAccountKeyCreation` blocks key creation. Never write code that expects
  a key file, and never call `GOOGLE_APPLICATION_CREDENTIALS` with a JSON key path.

Email ingestion is Gmail API + Pub/Sub push, **not** SendGrid — there is no owned domain
available for MX records, so the SendGrid Inbound Parse approach was abandoned in favor of
watching a Gmail inbox directly.

**Note on Pub/Sub and Eventarc delivery semantics:** both use at-least-once delivery. The same
event can and will be redelivered. Section 5 below specifies a mandatory Firestore pre-check
to make the pipeline idempotent against this — do not skip it, it has already caused a real
duplicate-processing incident in production.

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
│   ├── gmail_oauth_setup.py      # one-time local OAuth authorization
│   ├── gmail_watch_renew.py      # non-interactive weekly watch() renewal
│   └── seed_inventory.py         # seeds Firestore `inventory` collection with test data
├── test_pdfs/                    # sample PCN PDFs for manual/regression testing
├── docs/diagrams/                # architecture diagram sources + rendered PNGs
├── secrets/                      # gitignored, holds the Gmail OAuth client JSON
├── .github/workflows/
│   └── ci-validation.yml
├── .env.example
├── .env                          # gitignored, real local values, never committed
├── .gitignore
├── AGENTS.md
└── README.md
```

---

## 3. `ingestor/main.py` — Gmail Push Receiver

FastAPI server receiving Pub/Sub push messages (Gmail API change notifications, not raw email).

1. **Endpoint:** `POST /`, accepts Pub/Sub's push JSON envelope:
   ```json
   { "message": { "data": "<base64>", "messageId": "...", "publishTime": "..." }, "subscription": "..." }
   ```
2. **Auth verification:** Pub/Sub push includes a signed OIDC identity token in the
   `Authorization: Bearer <token>` header, issued for `pcn-agent-sa`. Verify this token
   (audience = the service's own `SERVICE_URL`, issuer = Google) before processing anything.
   Reject with `401` if invalid or missing.
3. **Decode payload:** `message.data` is base64 JSON: `{"emailAddress": "...", "historyId": "..."}`.
   This only signals *something changed* — the actual message must be fetched separately.
4. **Fetch new message(s) via Gmail API:**
   - Maintain `last_history_id` in Firestore (collection `gmail_sync_state`, single document).
     Seed from Firestore first; fall back to the current push payload's `historyId` if unset.
   - Call `users.history.list(userId="me", startHistoryId=<last known id>)` to find new
     messages added to `INBOX` since then.
   - For each new message: call `users.messages.get(userId="me", id=<msg id>)`.
   - Extract the `From` header (parse out just the email address) and check it against
     `ALLOWED_SENDERS` (env var, comma-separated, case-insensitive match). If not allowed,
     log the rejection and skip this message — do not download its attachment, do not upload
     to GCS. Still advance past it so it isn't reprocessed forever.
   - Update `last_history_id` in Firestore after each message is handled (whether processed
     or rejected).
5. **Extract PDF attachment:** walk the message payload parts for `mimeType ==
   "application/pdf"`, fetch via `users.messages.attachments.get`, decode from base64url.
6. **Upload to GCS:** upload the decoded PDF bytes to bucket `pcn-raw-documents`, object name
   `<gmail-message-id>.pdf`. This upload is what fires the existing Eventarc trigger — do not
   call the agent service directly, the two services stay fully decoupled.
7. **Auth for outbound calls:** Gmail API calls use OAuth (refresh token from env vars).
   Firestore/GCS calls use ADC — automatic via the attached `pcn-agent-sa` identity on Cloud
   Run, and via impersonation locally. No code branching needed for either environment.
8. **Response:** always return `200 OK` promptly. Do the GCS upload synchronously before
   returning — this path isn't expected to be slow enough to need backgrounding.
9. **`GET /healthz`:** always returns `200 {"status": "ok"}`. No OIDC verification on this
   route specifically — it's a plain liveness check.

---

## 4. `agent/tools.py` — Action Layer

Three tool functions, shared across the multi-agent pipeline (Section 5) — assigned to
whichever stage needs them, logic itself does not change per-stage:

1. **`query_firestore_inventory(part_number: str) -> dict`**
   Queries Firestore collection `inventory`. Exact document schema:
   ```json
   { "part_number": "str", "replacement_part_numbers": ["str"], "status": "str", "datasheet_uri": "str" }
   ```
   On no match, return exactly `{"found": false, "part_number": <input>}` — no other keys,
   no guessed data.

2. **`github_create_pr(repo_url: str, part_number: str, gcs_object_name: str, hal_modifications: list[dict]) -> dict`**
   Use `PyGithub` via the GitHub Contents API (`repo.update_file()` / `repo.create_file()`),
   not a local git clone. `hal_modifications` MUST be a **list of `{"path": str, "content":
   str}` objects**, never a `{path: content}` dictionary — passing the filename as a dict
   key exposes it to the LLM function-calling schema layer's key-sanitization, which silently
   mangles special characters (a `.` in `hal_bme280.h` was previously corrupted to `_`, and in
   one case to a zero-width space). Passing the filename as a value avoids this entirely.
   Compute the branch name deterministically in code, never left to LLM discretion:
   `branch = f"pcn/{part_number}-{hashlib.sha256(gcs_object_name.encode()).hexdigest()[:6]}"`.
   The exact expected filename pattern is `hal_<part_number_lowercase>.h` — make this explicit
   in the calling agent's instruction (Section 5) so the model has one deterministic target
   instead of inventing names across multiple attempts. If the branch already exists (retrigger
   of the same source document), reuse it — add new commit(s) to the existing branch/PR rather
   than opening a duplicate. Open a PR against `main` if one doesn't already exist for this
   branch. Auth via `GITHUB_TOKEN` (fine-grained PAT, `Contents: write` + `Pull requests:
   write` on the target repo only).
   *Known limitation:* each modified file still produces its own commit (Contents API
   behavior), not one atomic multi-file commit. Acceptable for single-file HAL updates; would
   need a Git Data (Trees) API rewrite for atomic multi-file commits if that becomes a
   requirement.

3. **`generate_eco_pdf(report_data: str, part_number: str) -> dict`**
   Generate a PDF (`reportlab`) and upload to bucket `eco-outputs`, object name
   `ECO-<UTC timestamp>.pdf`. Pass the actual current UTC timestamp into the report content
   explicitly — do not let the LLM invent a "Date Generated" value.

---

## 5. `agent/main.py` — Eventarc Handler + Multi-Agent Pipeline

### Architecture

The pipeline is a **manually orchestrated 3-stage sequence**, not ADK's built-in
`SequentialAgent` — empirical testing showed `SequentialAgent` combined with an outer retry
loop cannot resume mid-sequence after a failure without a resumable-session backend that
`InMemorySessionService` does not provide. Instead: three independent `Agent` + `Runner`
pairs, sharing one `session_id`, with the text output of each stage parsed as JSON in code
and explicitly re-injected as the `new_message` for the next stage.

```python
triage_agent = Agent(
    name="triage_agent",
    model="gemini-3.5-flash",
    instruction=STAGE1_INSTRUCTION,
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

session_service = InMemorySessionService()
triage_runner = Runner(agent=triage_agent, app_name="pcn_triage", session_service=session_service)
resolution_runner = Runner(agent=resolution_agent, app_name="pcn_triage", session_service=session_service)
action_runner = Runner(agent=action_agent, app_name="pcn_triage", session_service=session_service)
```

### Stage 1 — Triage
- Tools: none. Reads the natively-attached PDF via `Part.from_uri(gcs_uri,
  mime_type="application/pdf")` — full multimodal read, not text extraction, so scanned/
  image-only PDFs work correctly. May span multiple pages; instruction must explicitly say
  to check all pages, not assume page one has everything.
- Output: `{"parts": ["<part_number_1>", ...]}`. Empty list if no part numbers found — never
  force an output.

### Stage 2 — Resolution
- Tools: `query_firestore_inventory`.
- For each part from Stage 1, resolve against Firestore.
- Output: `{"parts": [{"part_number": ..., "found": bool, "replacement_part_numbers": [...],
  "datasheet_uri": ..., "status": ...}, ...]}`.
- **Must not invent or guess a replacement under any circumstances.** `found: false` is a
  valid, expected outcome, not a failure state.

### Stage 3 — Action
- Tools: `github_create_pr`, `generate_eco_pdf`.
- For each part where `found: true`: reason about the HAL change needed, call
  `github_create_pr` with `hal_modifications` as a list of `{"path": "hal_<part_lower>.h",
  "content": ...}` objects (see Section 4 — never a dict keyed by filename), then call
  `generate_eco_pdf`.
- For each part where `found: false`: take no action for that part — no PR, no ECO.
- Output per part: `{"part_number": ..., "replacement_found": bool, "pr_url": str|null,
  "eco_url": str|null, "status": "COMPLETED"|"NO_INVENTORY_MATCH"}`.

### Orchestration (`receive_event()`)
1. Verify the Eventarc-issued OIDC token (same mechanism as Section 3 step 2, audience =
   this service's own `SERVICE_URL`).
2. Read `ce-subject` header for the GCS object name; construct
   `gs://pcn-raw-documents/<object_name>`.
3. **Idempotency pre-check (mandatory, runs before anything else below):** query Firestore
   `agent_runs` for an existing document with this exact `gcs_uri`. If one exists with a
   terminal status (`COMPLETED`, `NO_INVENTORY_MATCH`, `COMPLETED_UNSTRUCTURED`,
   `REJECTED_SIZE`, `REJECTED_METADATA_ERROR`), log this as a duplicate delivery and return
   `200 OK` immediately — do not invoke Stage 1. This is required because Pub/Sub and
   Eventarc both use at-least-once delivery and will redeliver the same event; without this
   check, a redelivery re-runs the entire pipeline, doubling LLM spend and producing
   duplicate ECO PDFs. Use judgment on the edge case of a matching document in a
   non-terminal (in-progress) state arriving within a short window — bias toward not
   re-running expensive work.
4. **Cost guard:** check the PDF's size via the GCS SDK before invoking anything. If it
   exceeds 5MB, or if the size check itself fails (e.g. `blob.reload()` error), write a
   Firestore `agent_runs` entry with `status: "REJECTED_SIZE"` or
   `"REJECTED_METADATA_ERROR"` respectively and return `200 OK` without invoking the LLM.
   Fail closed — an unreadable size must not be treated as "small enough to proceed."
5. Run each stage via `run_stage_with_retry(runner, session_id, message)`: 3 attempts,
   exponential backoff, retrying only the failed stage — a Stage 2 failure must not re-invoke
   Stage 1's (expensive, multimodal) call. This has been validated against a real production
   failure (an `AssertionError` inside `github_create_pr`), confirmed via logs showing only
   the failed stage re-executed.
6. Parse each stage's JSON output defensively before passing it to the next stage: strip
   markdown code fences if present, and if parsing still fails, do not crash the whole run —
   log the raw response and fall back gracefully (see Firestore schema below,
   `status: "COMPLETED_UNSTRUCTURED"`).
7. Log each stage's outcome with a distinct prefix (`[TRIAGE]`, `[RESOLUTION]`, `[ACTION]`)
   at both start and completion — this is required, not optional, so Cloud Run logs clearly
   show the pipeline progressing through three stages. Also prefix retry/backoff log lines
   with the same stage tag.
8. After Stage 3, write one Firestore `agent_runs` document:
   ```json
   {
     "gcs_uri": "str",
     "target_repo": "str",
     "status": "str",
     "extracted_parts": [
       {
         "part_number": "str",
         "found": "bool",
         "replacement_found": "bool",
         "pr_url": "str|null",
         "eco_url": "str|null",
         "status": "COMPLETED|NO_INVENTORY_MATCH|COMPLETED_UNSTRUCTURED"
       }
     ],
     "timestamp": "<server timestamp>"
   }
   ```
9. Return `200 OK` with a JSON summary.
10. **`GET /healthz`:** always returns `200 {"status": "ok"}`, no OIDC check on this route.

---

## 6. Gmail Watch Renewal

`users.watch()` expires every ~7 days. `scripts/gmail_watch_renew.py` re-registers it
non-interactively using the stored refresh token (no browser login). Currently run manually;
package as a Cloud Run Job triggered weekly by Cloud Scheduler when time allows — until then,
this is a documented manual operational step, not a blocker.

---

## 7. Environment Variables

Read all of the following from environment variables — never hardcode any value in source
code:

```
GCP_PROJECT_ID
GCP_REGION
GITHUB_TOKEN
GITHUB_TARGET_REPO
GMAIL_CLIENT_ID
GMAIL_CLIENT_SECRET
GMAIL_REFRESH_TOKEN
GMAIL_WATCHED_ADDRESS
ALLOWED_SENDERS
GMAIL_PUBSUB_TOPIC
GCS_RAW_DOCUMENTS_BUCKET
GCS_ECO_OUTPUTS_BUCKET
SERVICE_URL
```

Never set or expect `GOOGLE_APPLICATION_CREDENTIALS` — all GCP auth is via ADC. Keep
`.env.example` listing these same names with placeholder (non-secret) values.

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
dependencies for both `ingestor/` and `agent/`, run `flake8` on both, run a basic import
smoke test (`python -c "import main"` in each directory). No deployment step in CI —
deployment stays manual via `gcloud run deploy`.

---

## 10. Deployment Commands (Reference)

```bash
gcloud run deploy pcn-ingestor --source ./ingestor --region asia-south1 --no-allow-unauthenticated --project pcn-orchestrator-2026
gcloud run deploy pcn-agent --source ./agent --region asia-south1 --no-allow-unauthenticated --project pcn-orchestrator-2026
```

Both services keep `--no-allow-unauthenticated`. The `roles/run.invoker` bindings for
`pcn-agent-sa` on both services are already in place and do not need repeating unless a
service is deleted and recreated. `SERVICE_URL` must be re-verified after any `--source`
redeploy — Cloud Run occasionally reports a different canonical URL format, and a stale
`SERVICE_URL` breaks OIDC audience verification (Section 3 step 2 / Section 5 step 1) with
`401` errors, not an obvious deploy failure.
