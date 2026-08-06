"""
Logs Blueprint — read-only access to the '## Log' section of each OU's
Daily files (<OU>/Daily/<date>.md). The root-level logs/ folder this used
to read from is legacy/unused; see docs/CORPUS_SCHEMA.md.

  GET /api/logs                   list Daily files with a non-empty Log section (newest first)
  GET /api/logs/content?path=...  just the '## Log' section content of one Daily file
"""

import logging
import os
import re

from flask import Blueprint, jsonify, request

import config
from auth_utils import require_perm

logger = logging.getLogger(__name__)

logs_bp = Blueprint("logs", __name__)

_SKIP_DIRS = frozenset(["db", "logs", "archive", "Archive", ".git", "__pycache__"])
_DAILY_FILENAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_ous() -> list[str]:
    try:
        return sorted(
            d for d in os.listdir(config.USER_DATA_ROOT)
            if d not in _SKIP_DIRS and os.path.isdir(os.path.join(config.USER_DATA_ROOT, d))
        )
    except FileNotFoundError:
        return []


def _extract_section(content: str, heading: str) -> str:
    """Body text between '## <heading>' and the next '## ' heading (or EOF)."""
    marker_re = re.compile(rf"(?m)^## {re.escape(heading)}\s*$")
    m = marker_re.search(content)
    if not m:
        return ""
    rest = content[m.end():]
    next_m = re.search(r"(?m)^## ", rest)
    return (rest[:next_m.start()] if next_m else rest).strip()


def _has_real_content(log_section: str) -> bool:
    """True if the Log section has anything beyond bare '### Morning'/'### Evening' headers."""
    stripped_lines = [
        line for line in log_section.splitlines()
        if line.strip() and not line.strip().startswith("###")
    ]
    return bool(stripped_lines)


def _scan_daily_dir(daily_dir: str, ou: str, rel_prefix: str, result: list[dict]) -> None:
    try:
        entries = os.scandir(daily_dir)
    except FileNotFoundError:
        return
    for entry in entries:
        m = _DAILY_FILENAME_RE.match(entry.name)
        if not entry.is_file() or not m:
            continue
        try:
            content = open(entry.path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        log_section = _extract_section(content, "Log")
        if not _has_real_content(log_section):
            continue
        y, mo, d = m.groups()
        result.append({
            "filename": entry.name,
            "rel_path": f"{rel_prefix}/{entry.name}",
            "ou": ou,
            "type": "daily",
            "date": f"{d}-{mo}-{y}",
            "sort_key": f"{y}-{mo}-{d}",
        })


def _list_logs() -> list[dict]:
    result = []
    for ou in _find_ous():
        ou_root = os.path.join(config.USER_DATA_ROOT, ou)
        _scan_daily_dir(os.path.join(ou_root, "Daily"), ou, f"{ou}/Daily", result)

        # Archived Daily files (housekeeping.archive_old_daily moves files >365
        # days old to <OU>/Archive/Daily/<YYYY>/<date>.md) — without this they
        # vanish from the Logs view entirely even though the Log content is
        # still there on disk.
        archive_daily_dir = os.path.join(ou_root, "Archive", "Daily")
        try:
            year_dirs = os.scandir(archive_daily_dir)
        except FileNotFoundError:
            year_dirs = []
        for year_entry in year_dirs:
            if not year_entry.is_dir():
                continue
            _scan_daily_dir(
                year_entry.path, ou,
                f"{ou}/Archive/Daily/{year_entry.name}", result,
            )
    result.sort(key=lambda x: x["sort_key"], reverse=True)
    for r in result:
        del r["sort_key"]
    return result


def _safe_daily_abs(rel_path: str) -> str | None:
    """
    Resolved absolute path only if it's a <OU>/Daily/<date>.md or an archived
    <OU>/Archive/Daily/<YYYY>/<date>.md file inside USER_DATA_ROOT.
    """
    norm = os.path.normpath(
        os.path.join(config.USER_DATA_ROOT, rel_path.replace("\\", "/").lstrip("/"))
    )
    root = os.path.normpath(config.USER_DATA_ROOT)
    if not norm.startswith(root + os.sep):
        return None

    parts = rel_path.replace("\\", "/").strip("/").split("/")
    is_daily = len(parts) == 3 and parts[1] == "Daily" and _DAILY_FILENAME_RE.match(parts[2])
    is_archived_daily = (
        len(parts) == 5
        and parts[1] == "Archive"
        and parts[2] == "Daily"
        and re.fullmatch(r"\d{4}", parts[3])
        and _DAILY_FILENAME_RE.match(parts[4])
    )
    return norm if (is_daily or is_archived_daily) else None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@logs_bp.get("/api/logs")
@require_perm("logs:read")
def list_logs():
    logs = _list_logs()
    return jsonify({"logs": logs, "total": len(logs)})


@logs_bp.get("/api/logs/content")
@require_perm("logs:read")
def log_content():
    rel = (request.args.get("path") or "").strip()
    if not rel:
        return jsonify({"error": "path is required"}), 400

    abs_path = _safe_daily_abs(rel)
    if not abs_path:
        return jsonify({"error": "Invalid path"}), 400

    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except FileNotFoundError:
        return jsonify({"error": "Not found"}), 404

    return jsonify({"rel_path": rel, "content": _extract_section(content, "Log")})
