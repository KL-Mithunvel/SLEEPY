"""
Logs Blueprint — read-only access to daily and weekly log files.

  GET /api/logs                   list log files (newest first)
  GET /api/logs/content?path=...  raw content of a single log file
"""

import logging
import os
import re

from flask import Blueprint, jsonify, request

import config
from auth_utils import require_perm

logger = logging.getLogger(__name__)

logs_bp = Blueprint("logs", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _logs_dir() -> str:
    return os.path.join(config.USER_DATA_ROOT, "logs")


def _classify(filename: str) -> tuple[str, str]:
    """Return (log_type, display_date) for a log filename."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})\.md$", filename)
    if m:
        y, mo, d = m.groups()
        return "daily", f"{d}-{mo}-{y}"
    m = re.match(r"^(\d{4})-(W\d{2})\.md$", filename)
    if m:
        y, w = m.groups()
        return "weekly", f"{w} {y}"
    return "other", os.path.splitext(filename)[0]


def _list_logs() -> list[dict]:
    logs_dir = _logs_dir()
    result = []
    try:
        for entry in os.scandir(logs_dir):
            if entry.is_file() and entry.name.endswith(".md"):
                log_type, display_date = _classify(entry.name)
                result.append({
                    "filename": entry.name,
                    "rel_path": f"logs/{entry.name}",
                    "type": log_type,
                    "date": display_date,
                })
    except FileNotFoundError:
        pass
    result.sort(key=lambda x: x["filename"], reverse=True)
    return result


def _safe_log_abs(rel_path: str) -> str | None:
    """Return resolved absolute path only if it's inside the logs/ dir and ends in .md."""
    norm = os.path.normpath(
        os.path.join(config.USER_DATA_ROOT, rel_path.replace("\\", "/").lstrip("/"))
    )
    logs_dir = os.path.normpath(_logs_dir())
    if norm.startswith(logs_dir + os.sep) and norm.endswith(".md"):
        return norm
    return None


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

    abs_path = _safe_log_abs(rel)
    if not abs_path:
        return jsonify({"error": "Invalid path"}), 400

    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except FileNotFoundError:
        return jsonify({"error": "Not found"}), 404

    return jsonify({"rel_path": rel, "content": content})
