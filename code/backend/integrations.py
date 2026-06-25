"""
External integrations: Telegram, Office 365 email, Jira Cloud.

All functions check for required config before making any network call.
Missing config → log a warning and return without error (opt-in by design).

Public API:
    send_telegram(message, chat_id=None) -> bool
    send_email(to, subject, body_text, *, body_html=None) -> bool
    jira_create(project_key, summary, description, issue_type='Task') -> str | None
    jira_sync_corpus(data_root, user_nick) -> dict
"""

import base64
import json
import logging
import os
import re
import urllib.request
import urllib.error
from datetime import date, timedelta, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_json(url: str, *, method: str = "GET", headers: dict | None = None,
               body: dict | None = None, timeout: int = 15) -> dict:
    """Minimal urllib wrapper. Returns parsed JSON dict. Raises on HTTP error."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(message: str, chat_id: str | None = None) -> bool:
    """
    Send a Telegram message via Bot API.
    Returns True on success, False if not configured or on error.
    """
    token = config.TELEGRAM_BOT_TOKEN
    cid = chat_id or config.TELEGRAM_DEFAULT_CHAT_ID
    if not token or not cid:
        logger.warning("send_telegram: TELEGRAM_BOT_TOKEN or TELEGRAM_DEFAULT_CHAT_ID not set")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": cid, "text": message, "parse_mode": "Markdown"}
    try:
        result = _http_json(url, method="POST", body=payload)
        ok = bool(result.get("ok"))
        if not ok:
            logger.error("send_telegram: API returned ok=false: %s", result)
        return ok
    except Exception:
        logger.exception("send_telegram: request failed")
        return False


# ---------------------------------------------------------------------------
# Office 365 email (MSAL + Graph API)
# ---------------------------------------------------------------------------

# Module-level MSAL app cache (one instance per process)
_msal_app = None


def _get_msal_app():
    global _msal_app
    if _msal_app is not None:
        return _msal_app
    try:
        import msal
    except ImportError:
        raise RuntimeError("msal package not installed — run: uv pip install msal")
    if not all([config.O365_CLIENT_ID, config.O365_CLIENT_SECRET, config.O365_TENANT_ID]):
        raise RuntimeError("O365_CLIENT_ID / O365_CLIENT_SECRET / O365_TENANT_ID not configured")
    _msal_app = msal.ConfidentialClientApplication(
        config.O365_CLIENT_ID,
        client_credential=config.O365_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{config.O365_TENANT_ID}",
    )
    return _msal_app


def _get_graph_token() -> str:
    app = _get_msal_app()
    scopes = ["https://graph.microsoft.com/.default"]
    result = app.acquire_token_for_client(scopes=scopes)
    if "access_token" not in result:
        raise RuntimeError(f"MSAL token acquisition failed: {result.get('error_description')}")
    return result["access_token"]


def send_email(to: str, subject: str, body_text: str, *, body_html: str | None = None) -> bool:
    """
    Send an email via Microsoft Graph API using MSAL client credentials.
    Returns True on success, False if not configured or on error.
    """
    if not all([config.O365_CLIENT_ID, config.O365_CLIENT_SECRET, config.O365_TENANT_ID, config.O365_MAILBOX]):
        logger.warning("send_email: O365 credentials not fully configured")
        return False

    try:
        token = _get_graph_token()
        content_type = "HTML" if body_html else "Text"
        body_content = body_html if body_html else body_text
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": content_type, "content": body_content},
                "toRecipients": [{"emailAddress": {"address": to}}],
            },
            "saveToSentItems": "true",
        }
        mailbox = config.O365_MAILBOX
        url = f"https://graph.microsoft.com/v1.0/users/{mailbox}/sendMail"
        _http_json(url, method="POST", headers={"Authorization": f"Bearer {token}"}, body=payload)
        logger.info("send_email: sent to=%s subject=%r", to, subject)
        return True
    except Exception:
        logger.exception("send_email: failed")
        return False


# ---------------------------------------------------------------------------
# Jira Cloud
# ---------------------------------------------------------------------------

def _jira_auth_header() -> str:
    raw = f"{config.JIRA_USER_EMAIL}:{config.JIRA_API_TOKEN}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def _jira_configured() -> bool:
    return bool(config.JIRA_BASE_URL and config.JIRA_USER_EMAIL and config.JIRA_API_TOKEN)


def jira_create(
    project_key: str,
    summary: str,
    description: str,
    issue_type: str = "Task",
) -> str | None:
    """
    Create a Jira Cloud issue. Returns the new issue key (e.g. 'PROJ-42') or None on error.
    """
    if not _jira_configured():
        logger.warning("jira_create: Jira not configured")
        return None

    url = f"{config.JIRA_BASE_URL.rstrip('/')}/rest/api/3/issue"
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph",
                              "content": [{"type": "text", "text": description}]}],
            },
            "issuetype": {"name": issue_type},
        }
    }
    try:
        result = _http_json(url, method="POST",
                            headers={"Authorization": _jira_auth_header()}, body=payload)
        key = result.get("key")
        if key:
            logger.info("jira_create: created %s — %s", key, summary)
        else:
            logger.error("jira_create: unexpected response: %s", result)
        return key
    except Exception:
        logger.exception("jira_create: failed")
        return None


def _jira_issue_status(issue_key: str) -> str | None:
    """Fetch an issue's status category name. Returns None on error or missing config."""
    if not _jira_configured():
        return None
    url = (f"{config.JIRA_BASE_URL.rstrip('/')}/rest/api/3/issue/{issue_key}"
           f"?fields=status")
    try:
        data = _http_json(url, headers={"Authorization": _jira_auth_header()})
        return data["fields"]["status"]["statusCategory"]["key"]
    except Exception:
        logger.exception("_jira_issue_status: failed for %s", issue_key)
        return None


# ---------------------------------------------------------------------------
# Jira corpus sync
# ---------------------------------------------------------------------------

_JIRA_TOKEN_RE = re.compile(r"JIRA:([A-Z][A-Z0-9_]+-\d+)")
_TASK_OPEN_RE = re.compile(r"^(\s*- \[ \] )(.+)$")
_SKIP_DIRS = frozenset(["db", "logs", "archive", "Archive", ".git", "__pycache__"])

_IST = timezone(timedelta(hours=5, minutes=30))


def jira_sync_corpus(data_root: str, user_nick: str = "ADMIN") -> dict:
    """
    Scan all corpus MD files for JIRA:<KEY> tokens on open task lines.
    For each unique key, fetch status from Jira.
    If the issue is 'done' in Jira but still `- [ ]` in the corpus, mark it `- [x]`.
    Returns {"scanned": N, "updated": N, "errors": N}.
    """
    if not _jira_configured():
        logger.info("jira_sync_corpus: Jira not configured, skipping")
        return {"scanned": 0, "updated": 0, "errors": 0}

    root = Path(data_root)

    # Collect all open-task lines mentioning JIRA keys across the corpus
    # Structure: {issue_key: [(file_path, line_index, original_line), ...]}
    key_locations: dict[str, list[tuple[Path, int, str]]] = {}
    scanned = 0

    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name in _SKIP_DIRS:
            continue
        for dirpath, subdirs, files in os.walk(d):
            subdirs[:] = sorted(s for s in subdirs if s not in {"Archive", "archive"})
            for fname in sorted(files):
                if not fname.endswith(".md"):
                    continue
                fpath = Path(dirpath) / fname
                try:
                    lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
                    for i, line in enumerate(lines):
                        if not _TASK_OPEN_RE.match(line):
                            continue
                        for m in _JIRA_TOKEN_RE.finditer(line):
                            key = m.group(1)
                            key_locations.setdefault(key, []).append((fpath, i, line))
                    scanned += 1
                except Exception:
                    logger.exception("jira_sync_corpus: error reading %s", fpath)

    if not key_locations:
        return {"scanned": scanned, "updated": 0, "errors": 0}

    # Fetch statuses — deduplicated by issue key
    updated = 0
    errors = 0
    done_keys: set[str] = set()

    for key in key_locations:
        status_cat = _jira_issue_status(key)
        if status_cat is None:
            errors += 1
        elif status_cat == "done":
            done_keys.add(key)

    if not done_keys:
        return {"scanned": scanned, "updated": 0, "errors": errors}

    # Apply updates — group by file to avoid multiple reads
    file_updates: dict[Path, dict[int, str]] = {}
    for key in done_keys:
        for fpath, line_idx, orig_line in key_locations[key]:
            m = _TASK_OPEN_RE.match(orig_line)
            if not m:
                continue
            new_line = orig_line.replace("- [ ]", "- [x]", 1)
            file_updates.setdefault(fpath, {})[line_idx] = new_line

    changed_files: list[Path] = []
    for fpath, line_changes in file_updates.items():
        try:
            lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            for idx, new_line in line_changes.items():
                if idx < len(lines):
                    # Preserve original line ending
                    ending = "\n" if not lines[idx].endswith("\r\n") else "\r\n"
                    lines[idx] = new_line + ending
            fpath.write_text("".join(lines), encoding="utf-8")
            updated += len(line_changes)
            changed_files.append(fpath)
        except Exception:
            logger.exception("jira_sync_corpus: failed to update %s", fpath)
            errors += 1

    if changed_files:
        _commit_jira_sync(data_root, len(done_keys), updated)

    result = {"scanned": scanned, "updated": updated, "errors": errors}
    logger.info("jira_sync_corpus done: %s", result)
    return result


def _commit_jira_sync(data_root: str, keys_closed: int, lines_updated: int) -> None:
    try:
        import git
        from datetime import datetime
        ts = datetime.now(_IST).strftime("%Y-%m-%d")
        try:
            repo = git.Repo(data_root, search_parent_directories=False)
        except git.InvalidGitRepositoryError:
            return
        repo.git.add(A=True)
        if not repo.git.status("--porcelain").strip():
            return
        author = git.Actor("Arivu Baalan", "arivu@smtw.in")
        repo.index.commit(
            f"jira-sync: closed {keys_closed} issue(s), {lines_updated} task(s) ticked ({ts})",
            author=author,
            committer=author,
        )
        logger.info("jira_sync_corpus: committed")
    except Exception:
        logger.warning("jira_sync_corpus: git commit failed")
