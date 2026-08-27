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
    Queries Firestore for the given part number and returns inventory status,
    replacement parts, datasheet URIs, etc.
    """
    logger.info(
        "[RESOLUTION] query_firestore_inventory called for part: %s",
        part_number)
    db = _get_db()
    doc_ref = db.collection("inventory").document(part_number)
    doc = doc_ref.get()
    if not doc.exists:
        # Also try a collection query in case the document ID differs from
        # part_number
        results = (
            db.collection("inventory")
            .where("part_number", "==", part_number)
            .limit(1)
            .stream()
        )
        doc_list = list(results)
        if not doc_list:
            logger.warning(
                "Part number %s not found in inventory",
                part_number)
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
def github_create_pr(
        repo_url: str,
        part_number: str,
        gcs_object_name: str,
        hal_modifications: dict) -> dict:
    """
    Create a GitHub PR updating HAL header file(s) in the target repository.

    Uses the GitHub Contents API (via PyGithub) rather than a local git clone.
    Each file in hal_modifications is committed in a separate API call — not a single
    atomic commit. For the current use case of single-file HAL updates this is
    acceptable; if hal_modifications ever spans multiple files, each will appear as
    its own commit on the branch rather than one combined commit.

    Args:
        repo_url: Full GitHub repo URL or "owner/repo" slug.
        part_number: The part number being triaged (used for branch name).
        gcs_object_name: The name of the GCS object (used for branch name).
        hal_modifications: List of dictionaries representing the files to update.
                           Each dictionary must have 'path' (the file path, e.g. "hal_bme280.h")
                           and 'content' (the new file content as a string).
                           Example: [{"path": "hal_bme280.h", "content": "<full file content>"}]

    Returns:
        Dict with keys: pr_url, pr_number, branch, status.
    """
    if not GITHUB_TOKEN:
        return {"status": "error", "detail": "GITHUB_TOKEN not set"}

    import hashlib
    short_hash = hashlib.sha256(
        gcs_object_name.encode("utf-8")).hexdigest()[:6]
    branch = f"pcn/{part_number}-{short_hash}"

    # Normalise repo_url → "owner/repo"
    repo_slug = repo_url
    if repo_slug.startswith("https://github.com/"):
        repo_slug = repo_slug.replace("https://github.com/", "").rstrip("/")
    if repo_slug.endswith(".git"):
        repo_slug = repo_slug[:-4]

    # Convert hal_modifications to a unified format if it came as a dict
    modifications_list = []
    if isinstance(hal_modifications, dict):
        for k, v in hal_modifications.items():
            modifications_list.append({"path": k, "content": v})
    elif isinstance(hal_modifications, list):
        modifications_list = hal_modifications

    paths_to_log = [item.get("path", "") for item in modifications_list]
    logger.info("Creating PR on %s, branch=%s, files=%s",
                repo_slug, branch, paths_to_log)

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
    resolved_modifications = {}
    for item in modifications_list:
        file_path = item.get("path", "")
        new_content = item.get("content", "")
        
        if not file_path:
            continue
            
        # Clean up any weird unicode characters like zero-width spaces or quotes
        file_path = file_path.strip().strip("\u200b").strip("'").strip('"')

        # LLM function calling often sanitizes dict keys (properties), replacing '.' with '_'.
        # If the filename ends with '_h' or '_c', restore the dot extension.
        if file_path.endswith("_h"):
            file_path = file_path[:-2] + ".h"
        elif file_path.endswith("_c"):
            file_path = file_path[:-2] + ".c"
            
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

        # Ensure new_content is a string
        if not isinstance(new_content, (str, bytes)):
            if isinstance(new_content, dict) and "content" in new_content:
                new_content = str(new_content["content"])
            else:
                new_content = str(new_content)

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
        existing_prs = list(
            repo.get_pulls(
                state="open", head=f"{repo.owner.login}:{branch}"
            )
        )
        if existing_prs:
            pr = existing_prs[0]
            logger.info(
                "PR already exists for branch %s: %s",
                branch,
                pr.html_url)
            return {
                "status": "already_exists",
                "pr_url": pr.html_url,
                "pr_number": pr.number,
                "branch": branch,
                "message": "SUCCESS: PR already exists. DO NOT call this tool again for this part."}

        pr = repo.create_pull(
            title=f"[PCN Triage] HAL update — {branch}",
            body=(
                "Automated PR created by the PCN Triage Orchestrator agent.\n\n"
                "**Modified files:**\n" +
                "\n".join(
                    f"- `{p}`" for p in resolved_modifications)),
            head=branch,
            base="main",
        )
        logger.info("Opened PR #%d: %s", pr.number, pr.html_url)
        return {
            "status": "ok",
            "pr_url": pr.html_url,
            "pr_number": pr.number,
            "branch": branch,
            "message": "SUCCESS: PR created. DO NOT call this tool again for this part."}
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
                     (part numbers, replacements, justification, affected repos, etc.).
                     DO NOT include a fake "Date Generated" field, the tool handles
                     the timestamp automatically.

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
    story.append(
        Paragraph(
            "Source: PCN Triage Orchestrator (Automated)",
            styles["Normal"]))
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
        logger.info(
            "ECO PDF uploaded to %s (%d bytes)",
            gcs_uri,
            len(pdf_bytes))
        return {
            "status": "ok",
            "gcs_uri": gcs_uri,
            "object_name": object_name,
            "bucket": GCS_ECO_OUTPUTS_BUCKET,
        }
    except Exception as exc:
        logger.error("Failed to upload ECO PDF: %s", exc)
        return {"status": "error", "detail": str(exc)}
