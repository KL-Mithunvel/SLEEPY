"""
Safe AI MD edit flow.

Every AI-proposed file change goes through:
  1. validate_edit()       — security + sanity checks
  2. propose_edit()        — write ai_events row (accepted=NULL), return diff for user review
  3. apply_edit() / reject_edit() — user confirms or cancels

Public API:
    validate_edit(rel_path, new_content)           → raises ValueError on policy violation
    compute_diff(original, new_content, rel_path)  → unified diff string
    scan_diff_for_secrets(diff)                    → list of matched pattern names (added lines only)
    propose_edit(rel_path, new_content, summary, conn) → dict with event_id + diff
    apply_edit(event_id, conn)                     → commit sha (EditConflict if file changed since proposal)
    reject_edit(event_id, conn)                    → None
    corpus_git_lock()                              → context manager serialising commits across processes
    ensure_corpus_gitignore()                      → keeps db/ out of the corpus repo
    ai_actor()                                     → the git.Actor for every AI/background commit
"""

import contextlib
import difflib
import hashlib
import json
import logging
import os
import re
import sqlite3
import time

import git

import config

logger = logging.getLogger(__name__)

# Maximum new content size the AI is allowed to write in a single edit.
_MAX_EDIT_BYTES = 512 * 1024  # 512 KB

# Cross-process git lock — the web process (this module, project_editor) and
# the worker (task_handlers/goal_planner/housekeeping) all commit to the same
# corpus repo. Two concurrent commits collide on .git/index.lock and one of
# them 500s, so every commit site takes this lock first.
_GIT_LOCK_NAME = ".sleepy-git.lock"
_GIT_LOCK_TIMEOUT_SEC = 30
_GIT_LOCK_STALE_SEC = 300

# Derived app state that must never be committed into the corpus history —
# db/ holds the SQLite DB (password hashes, every ai_events row / chat log),
# the Chroma index and news-watch dedup state.
_CORPUS_GITIGNORE_LINES = ("db/", "*.db", "*.db-shm", "*.db-wal", "*.sqlite3")

# ---------------------------------------------------------------------------
# Secret scanning — corpus governance rule "no secrets in MD files" (charter
# §7). Deliberately conservative, high-confidence patterns only: false
# positives block a legitimate edit outright, so vague heuristics (bare
# base64/hex blobs, generic "password:" lines) are left out on purpose. Only
# *added* lines of a diff are scanned (see scan_diff_for_secrets) so a
# pre-existing false positive elsewhere in a file can't block unrelated edits
# to that same file forever.
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = (
    ("AWS access key",       re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Anthropic API key",    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("GitHub token",         re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token",          re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key",       re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe secret key",    re.compile(r"\bsk_live_[0-9a-zA-Z]{24,}\b")),
    ("private key block",    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")),
)


def scan_diff_for_secrets(diff: str) -> list[str]:
    """
    Scan only the *added* lines of a unified diff (lines starting with a
    single '+', not the '+++ b/...' file header) for high-confidence secret
    patterns. Returns matched pattern names (never the matched text itself —
    callers must not echo secret material back into an error message, log
    line, or ai_events row).
    """
    if not diff:
        return []
    added_text = "\n".join(
        line[1:] for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    if not added_text:
        return []
    return [name for name, pattern in _SECRET_PATTERNS if pattern.search(added_text)]


class EditConflict(ValueError):
    """The file changed on disk after the edit was proposed — applying would
    silently discard whatever wrote it (a nightly job, a quick-capture, the
    Projects editor). Callers surface this as 409 so the user can re-propose."""


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_current(abs_path: str) -> str | None:
    """Current file text, or None if the file doesn't exist."""
    if not os.path.isfile(abs_path):
        return None
    with open(abs_path, encoding="utf-8", errors="replace") as f:
        return f.read()


@contextlib.contextmanager
def corpus_git_lock(data_root: str | None = None):
    """
    Serialise git operations on the corpus repo across processes. Uses an
    O_EXCL lock file (works on Windows and Linux, no extra dependency); a lock
    older than _GIT_LOCK_STALE_SEC is treated as abandoned by a crashed process.
    """
    root = data_root or config.USER_DATA_ROOT
    lock_path = os.path.join(root, _GIT_LOCK_NAME)
    deadline = time.monotonic() + _GIT_LOCK_TIMEOUT_SEC
    fd = None
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            break
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(lock_path)
            except OSError:
                age = 0
            if age > _GIT_LOCK_STALE_SEC:
                logger.warning("corpus_git_lock: removing stale lock (%.0fs old)", age)
                with contextlib.suppress(OSError):
                    os.remove(lock_path)
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"Timed out waiting for corpus git lock at {lock_path}")
            time.sleep(0.2)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        with contextlib.suppress(OSError):
            os.remove(lock_path)


def ensure_corpus_gitignore(data_root: str | None = None) -> bool:
    """
    Make sure the corpus repo ignores db/ (and this module's lock file) before
    any `git add -A`. Returns True if the .gitignore was created or extended.
    Belt-and-braces alongside the checked-in data/klm/.gitignore: a fresh
    data root on a new machine has no .gitignore at all.
    """
    root = data_root or config.USER_DATA_ROOT
    path = os.path.join(root, ".gitignore")
    existing = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            existing = f.read()
    present = {line.strip() for line in existing.splitlines()}
    missing = [l for l in (*_CORPUS_GITIGNORE_LINES, _GIT_LOCK_NAME) if l not in present]
    if not missing:
        return False
    with open(path, "a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write("# Derived app state — never part of the corpus history (added by md_editor)\n")
        f.write("\n".join(missing) + "\n")
    logger.info("ensure_corpus_gitignore: added %s to %s", missing, path)
    return True


def ai_actor() -> git.Actor:
    """The one identity every AI/background commit into the corpus repo uses."""
    return git.Actor("Arivu Baalan", "arivu@smtw.in")


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

    current = _read_current(abs_path)
    is_new = current is None
    original = current or ""

    diff = compute_diff(original, new_content, norm)

    secret_hits = scan_diff_for_secrets(diff)
    if secret_hits:
        logger.warning("propose_edit blocked for %s: possible secret(s): %s", norm, ", ".join(secret_hits))
        raise ValueError(
            f"Blocked: this edit adds what looks like a secret ({', '.join(secret_hits)}). "
            "Remove it and propose again — no secrets in the MD corpus."
        )

    # Store the pending edit payload in the diff column as JSON so apply_edit
    # can retrieve everything needed without touching the filesystem again.
    # base_hash records what the file looked like at proposal time so apply_edit
    # can refuse to clobber a file something else wrote in the meantime.
    payload = json.dumps({
        "rel_path": norm,
        "new_content": new_content,
        "summary": summary,
        "diff": diff,
        "base_hash": None if is_new else _content_hash(original),
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
    Raises ValueError if no pending edit exists for event_id, or EditConflict
    (a ValueError subclass) if the file changed on disk since it was proposed.
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

    # Defense in depth: propose_edit already blocks this, but re-check here
    # too in case a pending row predates this check or was written some
    # other way — nothing lands in a git commit with a secret in it either way.
    secret_hits = scan_diff_for_secrets(payload.get("diff", ""))
    if secret_hits:
        logger.warning("apply_edit blocked for %s: possible secret(s): %s", norm, ", ".join(secret_hits))
        raise ValueError(
            f"Blocked: this edit adds what looks like a secret ({', '.join(secret_hits)}). "
            "Reject it and propose again — no secrets in the MD corpus."
        )

    abs_path = os.path.join(config.USER_DATA_ROOT, norm)

    with corpus_git_lock():
        # Stale-proposal guard: the file must still be what the diff was computed
        # against. Only enforced for proposals that recorded a base_hash (older
        # pending rows written before this field existed apply as before).
        if "base_hash" in payload:
            current = _read_current(abs_path)
            current_hash = None if current is None else _content_hash(current)
            if current_hash != payload["base_hash"]:
                raise EditConflict(
                    f"{norm} changed on disk after this edit was proposed — "
                    "discard it and ask again so the diff is computed against the current file"
                )

        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        # Git commit
        repo = _get_repo()
        ensure_corpus_gitignore()
        rel_to_repo = os.path.relpath(abs_path, config.USER_DATA_ROOT)
        repo.index.add([rel_to_repo])
        author = ai_actor()
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
