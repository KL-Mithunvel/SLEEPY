"""
Safe AI MD edit flow.

Every AI-proposed file change goes through:
  1. validate_edit()       — security + sanity checks
  2. propose_edit()        — write ai_events row (accepted=NULL), return diff for user review
  3. apply_edit() / reject_edit() — user confirms or cancels

Public API:
    validate_edit(rel_path, new_content)           → raises ValueError on policy violation
    compute_diff(original, new_content, rel_path)  → unified diff string
    propose_edit(rel_path, new_content, summary, conn) → dict with event_id + diff
    apply_edit(event_id, conn)                     → commit sha
    reject_edit(event_id, conn)                    → None
"""

import difflib
import json
import logging
import os
import sqlite3

import git

import config

logger = logging.getLogger(__name__)

# Maximum new content size the AI is allowed to write in a single edit.
_MAX_EDIT_BYTES = 512 * 1024  # 512 KB


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_path(rel_path: str) -> str:
    """
    Validate that rel_path is a safe .md path inside USER_DATA_ROOT.
    Returns the normalised relative path (forward slashes, no leading slash).
    Raises ValueError on any violation.
    """
    if not rel_path or not rel_path.strip():
        raise ValueError("rel_path is empty")

    # Absolute and drive-relative paths (e.g. /etc/passwd on Windows) are always rejected.
    # os.path.isabs() returns False for drive-relative paths on Windows Python 3.12+,
    # so we also check for a leading slash/backslash explicitly.
    if os.path.isabs(rel_path) or rel_path[:1] in ("/", "\\"):
        raise ValueError(f"Path traversal detected: {rel_path!r}")

    norm = rel_path.replace("\\", "/").lstrip("/")
    abs_path = os.path.normpath(os.path.join(config.USER_DATA_ROOT, norm))
    data_root = os.path.normpath(config.USER_DATA_ROOT)

    if not abs_path.startswith(data_root + os.sep) and abs_path != data_root:
        raise ValueError(f"Path traversal detected: {rel_path!r}")

    db_dir = os.path.normpath(os.path.join(data_root, "db"))
    if abs_path.startswith(db_dir + os.sep) or abs_path == db_dir:
        raise ValueError(f"Writes to db/ are not allowed: {rel_path!r}")

    if not norm.endswith(".md"):
        raise ValueError(f"Only .md files may be edited by AI: {rel_path!r}")

    return norm


def validate_edit(rel_path: str, new_content: str) -> None:
    """
    Raise ValueError if the proposed edit violates any safety policy.

    Rules:
    - rel_path must pass validate_path()
    - new_content must be a non-empty string under _MAX_EDIT_BYTES
    """
    validate_path(rel_path)

    if not new_content or not new_content.strip():
        raise ValueError("new_content is empty")

    if len(new_content.encode("utf-8")) > _MAX_EDIT_BYTES:
        raise ValueError(
            f"new_content exceeds size limit ({_MAX_EDIT_BYTES // 1024} KB)"
        )


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

def compute_diff(original: str, new_content: str, rel_path: str) -> str:
    """
    Compute a unified diff between original and new_content.
    Returns an empty string if there are no changes.
    """
    original_lines = original.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            original_lines,
            new_lines,
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
            lineterm="",
        )
    )
    return "\n".join(diff_lines)


# ---------------------------------------------------------------------------
# Git repo helper
# ---------------------------------------------------------------------------

def _get_repo() -> git.Repo:
    data_root = config.USER_DATA_ROOT
    try:
        return git.Repo(data_root, search_parent_directories=False)
    except git.InvalidGitRepositoryError:
        logger.info("Initialising git repo at %s", data_root)
        repo = git.Repo.init(data_root)
        return repo


# ---------------------------------------------------------------------------
# Propose / apply / reject
# ---------------------------------------------------------------------------

def propose_edit(
    rel_path: str,
    new_content: str,
    summary: str,
    conn: sqlite3.Connection,
) -> dict:
    """
    Validate + dry-run an AI edit:
    - Reads the current file (or treats it as empty if new)
    - Computes the unified diff
    - Writes a pending row to ai_events (accepted=NULL)

    Returns:
        {
            "event_id": int,
            "diff":     str,    # empty string if file is unchanged
            "rel_path": str,
            "summary":  str,
            "is_new":   bool,   # True if the file does not exist yet
        }

    Raises ValueError if validation fails (no ai_events row written in that case).
    """
    validate_edit(rel_path, new_content)

    norm = rel_path.replace("\\", "/").lstrip("/")
    abs_path = os.path.join(config.USER_DATA_ROOT, norm)

    if os.path.isfile(abs_path):
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            original = f.read()
        is_new = False
    else:
        original = ""
        is_new = True

    diff = compute_diff(original, new_content, norm)

    # Store the pending edit payload in the diff column as JSON so apply_edit
    # can retrieve everything needed without touching the filesystem again.
    payload = json.dumps({
        "rel_path": norm,
        "new_content": new_content,
        "summary": summary,
        "diff": diff,
    })

    cur = conn.execute(
        """
        INSERT INTO ai_events (event_type, diff)
        VALUES ('md_edit', ?)
        """,
        (payload,),
    )
    conn.commit()
    event_id = cur.lastrowid

    logger.info("Proposed edit event_id=%d for %s", event_id, norm)
    return {
        "event_id": event_id,
        "diff": diff,
        "rel_path": norm,
        "summary": summary,
        "is_new": is_new,
    }


def apply_edit(event_id: int, conn: sqlite3.Connection) -> str:
    """
    Apply a pending AI edit (accepted=NULL) to the filesystem and commit.

    Returns the git commit SHA.
    Raises ValueError if no pending edit exists for event_id.
    """
    row = conn.execute(
        "SELECT diff FROM ai_events WHERE id = ? AND event_type = 'md_edit' AND accepted IS NULL",
        (event_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"No pending md_edit for event_id={event_id}")

    payload = json.loads(row["diff"])
    norm = payload["rel_path"]
    new_content = payload["new_content"]
    summary = payload.get("summary", "AI edit")

    # Re-validate before touching the filesystem
    validate_edit(norm, new_content)

    abs_path = os.path.join(config.USER_DATA_ROOT, norm)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    # Git commit
    repo = _get_repo()
    rel_to_repo = os.path.relpath(abs_path, config.USER_DATA_ROOT)
    repo.index.add([rel_to_repo])
    author = git.Actor("Arivu Baalan", "arivu@smtw.in")
    commit = repo.index.commit(
        f"AI: {summary}",
        author=author,
        committer=author,
    )
    sha = commit.hexsha

    conn.execute(
        "UPDATE ai_events SET accepted = 1 WHERE id = ?",
        (event_id,),
    )
    conn.commit()

    logger.info("Applied edit event_id=%d → commit %s (%s)", event_id, sha[:8], norm)
    return sha


def reject_edit(event_id: int, conn: sqlite3.Connection) -> None:
    """Mark a pending AI edit as rejected (accepted=0)."""
    conn.execute(
        "UPDATE ai_events SET accepted = 0 WHERE id = ? AND accepted IS NULL",
        (event_id,),
    )
    conn.commit()
    logger.info("Rejected edit event_id=%d", event_id)
