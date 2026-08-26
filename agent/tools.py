"""
agent/tools.py — Action Layer for the PCN Triage Agent
=======================================================
Four tool functions callable by the ADK agent:

1. read_pcn_document          — download PDF from GCS and extract its text
2. query_firestore_inventory  — look up a part number in the inventory collection
3. github_create_pr           — open a HAL update PR on the target GitHub repo
4. generate_eco_pdf           — generate an ECO PDF report and upload to GCS
"""

import io
import logging
import os
from datetime import datetime, timezone

import pypdf
from github import Github, GithubException
from google.cloud import firestore, storage
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env vars (read at import time; validated at first call)
# ---------------------------------------------------------------------------
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GCS_ECO_OUTPUTS_BUCKET = os.environ.get("GCS_ECO_OUTPUTS_BUCKET", "")


# ---------------------------------------------------------------------------
# Tool 1: Read PCN document from GCS
# ---------------------------------------------------------------------------
def read_pcn_document(gcs_uri: str) -> dict:
    """Download a PDF from GCS and return its extracted text content.

    Args:
        gcs_uri: GCS URI of the PDF, e.g. gs://pcn-raw-documents/abc123.pdf

    Returns:
        dict with keys 'text' (str) and 'page_count' (int), or 'error' (str).
    """
    logger.info("Reading PCN document from %s", gcs_uri)
    try:
        # Parse gs://bucket/object
        without_scheme = gcs_uri.removeprefix("gs://")
        bucket_name, _, object_name = without_scheme.partition("/")

        bucket = _get_gcs().bucket(bucket_name)
        blob = bucket.blob(object_name)
        pdf_bytes = blob.download_as_bytes()

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        full_text = "\n".join(pages).strip()

        logger.info("Extracted %d chars from %d pages in %s",
                    len(full_text), len(pages), gcs_uri)
        return {"text": full_text, "page_count": len(pages)}
    except Exception as exc:
        logger.error("Failed to read PCN document %s: %s", gcs_uri, exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Shared GCP clients
# ---------------------------------------------------------------------------
_db: firestore.Client = None
_gcs: storage.Client = None


def _get_db() -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def _get_gcs() -> storage.Client:
    global _gcs
    if _gcs is None:
        _gcs = storage.Client()
    return _gcs


# ---------------------------------------------------------------------------
# Tool 1 — Firestore inventory lookup
# ---------------------------------------------------------------------------
def query_firestore_inventory(part_number: str) -> dict:
    """
    Query the Firestore 'inventory' collection for the given part number.

    Args:
        part_number: The exact part number string to look up (e.g. "INA226").

    Returns:
        A dict with keys: part_number, replacement_part_numbers, status,
        datasheet_uri.  If not found, returns {"found": False, "part_number": <...>}.
    """
    logger.info("Querying inventory for part_number=%s", part_number)
    db = _get_db()
    doc_ref = db.collection("inventory").document(part_number)
    doc = doc_ref.get()
    if not doc.exists:
        # Also try a collection query in case the document ID differs from part_number
        results = (
            db.collection("inventory")
            .where("part_number", "==", part_number)
            .limit(1)
            .stream()
        )
        doc_list = list(results)
        if not doc_list:
            logger.warning("Part number %s not found in inventory", part_number)
            return {"found": False, "part_number": part_number}
        data = doc_list[0].to_dict()
    else:
        data = doc.to_dict()

    data["found"] = True
    logger.info("Inventory result for %s: %s", part_number, data)
    return data


# ---------------------------------------------------------------------------
# Tool 2 — GitHub PR creation
# ---------------------------------------------------------------------------
def github_create_pr(repo_url: str, branch: str, hal_modifications: dict) -> dict:
    """
    Create a GitHub PR updating HAL header file(s) in the target repository.

    Uses the GitHub Contents API (via PyGithub) rather than a local git clone.
    Each file in hal_modifications is committed in a separate API call — not a single
    atomic commit. For the current use case of single-file HAL updates this is
    acceptable; if hal_modifications ever spans multiple files, each will appear as
    its own commit on the branch rather than one combined commit.

    Args:
        repo_url: Full GitHub repo URL or "owner/repo" slug.
        branch:   Name for the new branch (e.g. "pcn/update-INA226-hal").
        hal_modifications: Dict mapping file paths (relative to repo root)
                           to their new content strings.
                           Example: {"hal/hal_i2c.h": "<full file content>"}

    Returns:
        Dict with keys: pr_url, pr_number, branch, status.
    """
    if not GITHUB_TOKEN:
        return {"status": "error", "detail": "GITHUB_TOKEN not set"}

    # Normalise repo_url → "owner/repo"
    repo_slug = repo_url
    if repo_slug.startswith("https://github.com/"):
        repo_slug = repo_slug.replace("https://github.com/", "").rstrip("/")
    if repo_slug.endswith(".git"):
        repo_slug = repo_slug[:-4]

    logger.info("Creating PR on %s, branch=%s, files=%s", repo_slug, branch, list(hal_modifications.keys()))

    gh = Github(GITHUB_TOKEN)
    try:
        repo = gh.get_repo(repo_slug)
    except GithubException as exc:
        logger.error("Could not access repo %s: %s", repo_slug, exc)
        return {"status": "error", "detail": str(exc)}

    # Get the SHA of main to base the branch on
    try:
        main_ref = repo.get_git_ref("heads/main")
        base_sha = main_ref.object.sha
    except GithubException as exc:
        logger.error("Could not get main branch ref: %s", exc)
        return {"status": "error", "detail": str(exc)}

    # Create the new branch
    try:
        repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_sha)
        logger.info("Created branch %s at %s", branch, base_sha)
    except GithubException as exc:
        if "already exists" in str(exc).lower():
            logger.warning("Branch %s already exists, continuing", branch)
        else:
            logger.error("Failed to create branch %s: %s", branch, exc)
            return {"status": "error", "detail": str(exc)}

    # Commit each modified file
    # Resolve each file_path against the actual repo tree: if the agent
    # supplies a path like "hal/hal_ina219.h" but no "hal/" directory exists,
    # fall back to the bare filename at repo root ("hal_ina219.h").
    resolved_modifications = {}
    for file_path, new_content in hal_modifications.items():
        resolved_path = file_path
        if "/" in file_path:
            # Check whether this path (or any ancestor dir) exists in the repo
            try:
                repo.get_contents(file_path, ref="main")
                # File exists — use as-is
            except GithubException:
                # Path not found — strip to basename
                basename = file_path.rsplit("/", 1)[-1]
                logger.info(
                    "Path %s not found in repo; using root-level %s instead",
                    file_path, basename,
                )
                resolved_path = basename
        resolved_modifications[resolved_path] = new_content

    for file_path, new_content in resolved_modifications.items():
        try:
            try:
                existing = repo.get_contents(file_path, ref=branch)
                repo.update_file(
                    path=file_path,
                    message=f"chore(hal): PCN-driven update for {file_path}",
                    content=new_content,
                    sha=existing.sha,
                    branch=branch,
                )
            except GithubException:
                repo.create_file(
                    path=file_path,
                    message=f"chore(hal): PCN-driven create {file_path}",
                    content=new_content,
                    branch=branch,
                )
            logger.info("Committed %s to %s", file_path, branch)
        except GithubException as exc:
            logger.error("Failed to commit %s: %s", file_path, exc)
            return {"status": "error", "detail": str(exc)}

    # Open the PR — idempotent: return existing PR if one already exists for
    # this branch, rather than letting a 422 trigger the agent to retry with
    # a differently-named branch (which produces duplicate PRs).
    try:
        existing_prs = list(repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}"))
        if existing_prs:
            pr = existing_prs[0]
            logger.info("PR already exists for branch %s: %s", branch, pr.html_url)
            return {"status": "already_exists", "pr_url": pr.html_url,
                    "pr_number": pr.number, "branch": branch}

        pr = repo.create_pull(
            title=f"[PCN Triage] HAL update — {branch}",
            body=(
                "Automated PR created by the PCN Triage Orchestrator agent.\n\n"
                "**Modified files:**\n"
                + "\n".join(f"- `{p}`" for p in resolved_modifications)
            ),
            head=branch,
            base="main",
        )
        logger.info("Opened PR #%d: %s", pr.number, pr.html_url)
        return {"status": "ok", "pr_url": pr.html_url, "pr_number": pr.number, "branch": branch}
    except GithubException as exc:
        logger.error("Failed to create PR: %s", exc)
        return {"status": "error", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Tool 3 — ECO PDF generation + GCS upload
# ---------------------------------------------------------------------------
def generate_eco_pdf(report_data: str) -> dict:
    """
    Generate an Engineering Change Order (ECO) PDF from report_data and
    upload it to the eco-outputs GCS bucket.

    Args:
        report_data: A freeform string containing the full ECO report content
                     (part numbers, replacements, justification, affected repos, etc.)

    Returns:
        Dict with keys: gcs_uri, object_name, bucket, status.
    """
    if not GCS_ECO_OUTPUTS_BUCKET:
        return {"status": "error", "detail": "GCS_ECO_OUTPUTS_BUCKET not set"}

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    object_name = f"ECO-{timestamp}.pdf"

    logger.info("Generating ECO PDF: %s", object_name)

    # Build PDF in memory
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Engineering Change Order (ECO)", styles["Title"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Generated: {timestamp}", styles["Normal"]))
    story.append(Paragraph("Source: PCN Triage Orchestrator (Automated)", styles["Normal"]))
    story.append(Spacer(1, 24))

    for line in report_data.splitlines():
        stripped = line.strip()
        if stripped:
            story.append(Paragraph(stripped, styles["Normal"]))
        else:
            story.append(Spacer(1, 6))

    doc.build(story)
    pdf_bytes = buffer.getvalue()

    # Upload to GCS
    try:
        bucket = _get_gcs().bucket(GCS_ECO_OUTPUTS_BUCKET)
        blob = bucket.blob(object_name)
        blob.upload_from_string(pdf_bytes, content_type="application/pdf")
        gcs_uri = f"gs://{GCS_ECO_OUTPUTS_BUCKET}/{object_name}"
        logger.info("ECO PDF uploaded to %s (%d bytes)", gcs_uri, len(pdf_bytes))
        return {
            "status": "ok",
            "gcs_uri": gcs_uri,
            "object_name": object_name,
            "bucket": GCS_ECO_OUTPUTS_BUCKET,
        }
    except Exception as exc:
        logger.error("Failed to upload ECO PDF: %s", exc)
        return {"status": "error", "detail": str(exc)}
