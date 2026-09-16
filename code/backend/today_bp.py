"""
Today Blueprint — endpoints that power the Today View.

  GET  /api/today                 aggregated daily view: last briefing + active tasks
  POST /api/today/briefing        generate (or regenerate) the morning briefing via LLM
  POST /api/today/tasks/toggle    mark a curated task done
  POST /api/today/tasks/cancel    mark a curated task cancelled
  POST /api/today/tasks/promote   copy a project-backlog task into today's curated list
  POST /api/today/tasks/add       add an ad-hoc task straight to today's curated list
  POST /api/today/capture         quick-capture a line to inbox.md (auto-applied, no confirm step)
"""

import logging
import os
import re
from datetime import datetime

from flask import Blueprint, jsonify, request

import ai_client
import config
import md_editor
import task_scan
from auth_utils import require_perm

logger = logging.getLogger(__name__)

today_bp = Blueprint("today", __name__)

_VALID_PRIORITIES = ("high", "medium", "low")
_DUE_FORMAT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_LINE_LEN = 1000


def _single_line(text: str) -> str:
    """
    Captures and ad-hoc tasks are appended as ONE markdown list line. A pasted
    multi-line string would otherwise inject arbitrary lines — including new
    `## ` headings that the materialiser/housekeeping parsers key off — into
    inbox.md or a Daily file. Collapse all whitespace runs (incl. newlines).
    """
    return re.sub(r"\s+", " ", text or "").strip()[:_MAX_LINE_LEN]


# ---------------------------------------------------------------------------
# GET /api/today
# ---------------------------------------------------------------------------

@today_bp.get("/api/today")
@require_perm("ai:suggest")
def get_today():
    """
    Returns:
      briefing      — text of the last morning briefing (or null)
      briefing_at   — formatted IST timestamp (DD-MM-YYYY HH:MM) or null
      tasks         — list of {rel_path, text, ou, due} — today's curated tasks
                      (from <OU>/Daily/<today>.md, not every project's backlog)

    Time-based freshness: the 06:30 IST cron (morning_briefing task) is the
    normal source of a same-day briefing, but if nobody's visited since
    before it last ran (server was down, first deploy, etc.) the stored
    briefing could be from a previous day. Rather than show stale content,
    the first page load of a new day regenerates it right here — once per
    day, not once per request, gated on the stored briefing's calendar date.
    """
    db = _db()

    row = _last_briefing_row(db)

    if row is None or row["created_at"][:10] != datetime.now().strftime("%Y-%m-%d"):
        try:
            briefing = ai_client.generate_morning_briefing(db)
            briefing_at = datetime.now().strftime("%d-%m-%Y %H:%M")
        except Exception:
            logger.exception("On-demand briefing refresh failed — falling back to last stored briefing")
            briefing, briefing_at = _format_briefing_row(row)
    else:
        briefing, briefing_at = _format_briefing_row(row)

    try:
        tasks = task_scan.scan_todays_tasks(config.USER_DATA_ROOT)
    except Exception as exc:
        logger.warning("Task scan failed: %s", exc)
        tasks = []

    return jsonify({"briefing": briefing, "briefing_at": briefing_at, "tasks": tasks})


# ---------------------------------------------------------------------------
# POST /api/today/briefing
# ---------------------------------------------------------------------------

@today_bp.post("/api/today/briefing")
@require_perm("ai:suggest")
def generate_briefing():
    """Generate (or regenerate) the morning briefing using RAG + LLM."""
    db = _db()
    try:
        briefing = ai_client.generate_morning_briefing(db)
    except Exception:
        logger.exception("Briefing generation failed")
        return jsonify({"error": "Briefing generation failed"}), 502

    now_str = datetime.now().strftime("%d-%m-%Y %H:%M")
    return jsonify({"briefing": briefing, "generated_at": now_str})


# ---------------------------------------------------------------------------
# POST /api/today/tasks/toggle
# ---------------------------------------------------------------------------

@today_bp.post("/api/today/tasks/toggle")
@require_perm("ai:edit_md")
def toggle_task():
    """
    Mark a single task line done (auto-applied, no confirm step).
    Request body: {"rel_path": "OU/project.md", "text": "exact task text"}
    """
    body = request.get_json(silent=True) or {}
    rel_path = (body.get("rel_path") or "").strip()
    text = (body.get("text") or "").strip()
    if not rel_path or not text:
        return jsonify({"error": "rel_path and text are required"}), 400

    db = _db()
    ok = task_scan.toggle_task(config.USER_DATA_ROOT, rel_path, text, db)
    if not ok:
        return jsonify({"error": "Task line not found — it may have changed, try refreshing"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# POST /api/today/tasks/cancel
# ---------------------------------------------------------------------------

@today_bp.post("/api/today/tasks/cancel")
@require_perm("ai:edit_md")
def cancel_task():
    """
    Mark a single task line cancelled — `- [-]`, distinct from done — so it
    stops resurfacing without being falsely recorded as completed (auto-applied,
    no confirm step). Request body: {"rel_path": "OU/project.md", "text": "exact task text"}
    """
    body = request.get_json(silent=True) or {}
    rel_path = (body.get("rel_path") or "").strip()
    text = (body.get("text") or "").strip()
    if not rel_path or not text:
        return jsonify({"error": "rel_path and text are required"}), 400

    db = _db()
    ok = task_scan.cancel_task(config.USER_DATA_ROOT, rel_path, text, db)
    if not ok:
        return jsonify({"error": "Task line not found — it may have changed, try refreshing"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# POST /api/today/tasks/promote
# ---------------------------------------------------------------------------

@today_bp.post("/api/today/tasks/promote")
@require_perm("ai:edit_md")
def promote_task():
    """
    Copy one open task from a project's own backlog into today's curated
    Active Tasks list (auto-applied, no confirm step). The project's own
    line is left untouched — this adds a same-day copy alongside it.
    Request body: {"rel_path": "OU/project.md", "text": "exact task text"}
    """
    body = request.get_json(silent=True) or {}
    rel_path = (body.get("rel_path") or "").strip()
    text = (body.get("text") or "").strip()
    if not rel_path or not text:
        return jsonify({"error": "rel_path and text are required"}), 400

    db = _db()
    ok = task_scan.promote_task(config.USER_DATA_ROOT, rel_path, text, db)
    if not ok:
        return jsonify({"error": "Task line not found — it may have changed, try refreshing"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# POST /api/today/tasks/add
# ---------------------------------------------------------------------------

@today_bp.post("/api/today/tasks/add")
@require_perm("ai:edit_md")
def add_task():
    """
    Add an ad-hoc task straight to today's Active Tasks list, tagged to an
    existing project (auto-applied, no confirm step).
    Request body: {"project_rel_path": "OU/project.md", "text": "...",
                    "priority": "high"|"medium"|"low"|null, "due": "YYYY-MM-DD"|null}
    """
    body = request.get_json(silent=True) or {}
    project_rel_path = (body.get("project_rel_path") or "").strip()
    text = _single_line(body.get("text"))
    priority = (body.get("priority") or "").strip().lower() or None
    due = (body.get("due") or "").strip() or None

    if not project_rel_path or not text:
        return jsonify({"error": "project_rel_path and text are required"}), 400
    if priority and priority not in _VALID_PRIORITIES:
        return jsonify({"error": "priority must be high, medium, or low"}), 400
    if due and not _DUE_FORMAT_RE.match(due):
        return jsonify({"error": "due must be YYYY-MM-DD"}), 400

    db = _db()
    ok = task_scan.add_task(config.USER_DATA_ROOT, project_rel_path, text, priority, due, db)
    if not ok:
        return jsonify({"error": "Could not add task — invalid project path"}), 400
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# POST /api/today/capture
# ---------------------------------------------------------------------------

@today_bp.post("/api/today/capture")
@require_perm("ai:edit_md")
def capture():
    """
    Append a quick-capture line to inbox.md and commit immediately.
    No user confirmation step — capture is always auto-applied.

    Request body: {"text": "some thought or task"}
    Response:     {"ok": true, "line": "- [ ] ...", "sha": "abcd1234"}
    """
    body = request.get_json(silent=True) or {}
    text = _single_line(body.get("text"))
    if not text:
        return jsonify({"error": "text is required"}), 400

    ts = datetime.now().strftime("%d-%m-%Y %H:%M")
    line = f"- [ ] {text}  <!-- captured {ts} -->"

    inbox_rel = "inbox.md"
    inbox_abs = os.path.join(config.USER_DATA_ROOT, inbox_rel)
    if os.path.isfile(inbox_abs):
        with open(inbox_abs, encoding="utf-8", errors="replace") as f:
            current = f.read()
    else:
        current = "# Inbox\n\nQuick captures land here.\n\n"

    new_content = current.rstrip("\n") + "\n" + line + "\n"

    db = _db()
    try:
        proposal = md_editor.propose_edit(inbox_rel, new_content, f"Capture: {text[:60]}", db)
        sha = md_editor.apply_edit(proposal["event_id"], db)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        logger.exception("Capture failed")
        return jsonify({"error": "Capture failed"}), 500

    return jsonify({"ok": True, "line": line, "sha": sha[:8]})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db():
    from app import get_db
    return get_db()


def _last_briefing_row(db):
    """Last non-voided morning briefing that has generated text, or None."""
    return db.execute(
        """
        SELECT result, created_at FROM ai_events
        WHERE event_type = 'morning_briefing' AND result IS NOT NULL AND voided = 0
        ORDER BY created_at DESC LIMIT 1
        """
    ).fetchone()


def _format_briefing_row(row):
    """(briefing_text, formatted_DD-MM-YYYY_HH:MM) from an ai_events row, or (None, None)."""
    if row is None:
        return None, None
    try:
        dt = datetime.strptime(row["created_at"][:16], "%Y-%m-%d %H:%M")
        briefing_at = dt.strftime("%d-%m-%Y %H:%M")
    except (ValueError, TypeError):
        briefing_at = row["created_at"]
    return row["result"], briefing_at
