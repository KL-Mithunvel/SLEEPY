"""
Corpus Blueprint — endpoints for news watch, materialiser, housekeeping, and corpus management.

Endpoints:
  POST /api/corpus/materialise               — manually trigger materialise_all
  POST /api/corpus/housekeeping              — manually trigger run_housekeeping
  GET  /api/corpus/housekeeping/results      — last housekeeping result from ai_events
  POST /api/corpus/news-watch                — manually trigger news_watch_submit
  GET  /api/corpus/news-watch/status         — poll current batch status
  GET  /api/corpus/news-items                — recent news items (parsed, for Today view)
  GET  /api/corpus/news-feedback             — list all feedback entries
  POST /api/corpus/news-feedback             — submit +1 / -1 rating for a bullet
"""

import logging
import re

from flask import Blueprint, jsonify, request

import config
import news_watch
import task_queue
from auth_utils import require_perm

_TITLE_URL_RE = re.compile(r'\[(.+?)\]\((https?://[^\)]+)\)')
_DOMAIN_DATE_RE = re.compile(r'—\s*([\w.\-]+)\s+\((\d{4}-\d{2}-\d{2})\)')
_TOPIC_RE = re.compile(r'\*topic:\s*([^*|]+?)\s*\*')
_REASON_RE = re.compile(r'\*relevant because:\s*([^*]+?)\s*\*')

logger = logging.getLogger(__name__)

corpus_bp = Blueprint("corpus", __name__)


def _db():
    from app import get_db
    return get_db()


# ---------------------------------------------------------------------------
# Materialiser — manual trigger
# ---------------------------------------------------------------------------

@corpus_bp.post("/api/corpus/materialise")
@require_perm("corpus:materialise")
def materialise_now():
    """Enqueue an immediate materialise task."""
    db = _db()
    task_id = task_queue.enqueue(db, "materialise", {"user_nick": config.USER_NICK})
    return jsonify({"task_id": task_id, "message": "Materialise queued"}), 202


# ---------------------------------------------------------------------------
# Housekeeping — manual trigger
# ---------------------------------------------------------------------------

@corpus_bp.post("/api/corpus/housekeeping")
@require_perm("corpus:housekeeping")
def housekeeping_now():
    """Enqueue an immediate housekeeping task."""
    db = _db()
    task_id = task_queue.enqueue(db, "housekeeping", {"user_nick": config.USER_NICK})
    return jsonify({"task_id": task_id, "message": "Housekeeping queued"}), 202


@corpus_bp.get("/api/corpus/housekeeping/results")
@require_perm("corpus:housekeeping")
def housekeeping_results():
    """Return the last housekeeping ai_events log row (if any)."""
    db = _db()
    row = db.execute(
        """
        SELECT id, prompt_hash, created_at, diff
        FROM ai_events
        WHERE event_type = 'housekeeping'
          AND voided = 0
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return jsonify({"last_run": None})
    import json
    try:
        data = json.loads(row["diff"] or "{}")
    except Exception:
        data = {}
    return jsonify({"last_run": {"id": row["id"], "at": row["created_at"], "result": data}})


# ---------------------------------------------------------------------------
# News Watch — submit
# ---------------------------------------------------------------------------

@corpus_bp.post("/api/corpus/news-watch")
@require_perm("corpus:news_watch")
def news_watch_trigger():
    """Manually trigger a news watch submit for the current day."""
    body = request.get_json(silent=True) or {}
    db = _db()
    task_id = task_queue.enqueue(db, "news_watch_submit", {"ou": body.get("ou", "")})
    return jsonify({"task_id": task_id, "message": "News watch queued"}), 202


# ---------------------------------------------------------------------------
# News Watch — status
# ---------------------------------------------------------------------------

@corpus_bp.get("/api/corpus/news-watch/status")
@require_perm("corpus:news_watch")
def news_watch_status():
    """Poll the current news batch status."""
    status = news_watch.get_batch_status(config.USER_DATA_ROOT)
    return jsonify(status)


# ---------------------------------------------------------------------------
# News items — parsed, for Today view sidebar
# ---------------------------------------------------------------------------

@corpus_bp.get("/api/corpus/news-items")
@require_perm("corpus:news_watch")
def news_items():
    """
    Return the most recent N news items from news_seen.json, parsed into
    structured objects for easy rendering.
    """
    limit = min(int(request.args.get("limit", 20)), 100)
    seen = news_watch.get_feedback(config.USER_DATA_ROOT)
    recent = list(reversed(seen))[:limit]

    items = []
    for entry in recent:
        bullet = entry.get("bullet", "")
        parsed = _parse_bullet(bullet)
        parsed["feedback"] = entry.get("feedback")
        parsed["date"] = entry.get("date", "")
        parsed["bullet"] = bullet
        items.append(parsed)

    return jsonify({"items": items, "total": len(seen)})


def _parse_bullet(bullet: str) -> dict:
    title, url, domain, pub_date, topic, reason = "", "", "", "", "", ""
    m = _TITLE_URL_RE.search(bullet)
    if m:
        title, url = m.group(1), m.group(2)
    m = _DOMAIN_DATE_RE.search(bullet)
    if m:
        domain, pub_date = m.group(1), m.group(2)
    m = _TOPIC_RE.search(bullet)
    if m:
        topic = m.group(1).strip()
    m = _REASON_RE.search(bullet)
    if m:
        reason = m.group(1).strip()
    return {"title": title, "url": url, "domain": domain, "pub_date": pub_date,
            "topic": topic, "reason": reason}


# ---------------------------------------------------------------------------
# News Feedback — list
# ---------------------------------------------------------------------------

@corpus_bp.get("/api/corpus/news-feedback")
@require_perm("corpus:news_watch")
def news_feedback_list():
    """Return all news_seen entries (bullet + feedback rating)."""
    seen = news_watch.get_feedback(config.USER_DATA_ROOT)
    # Return in reverse chronological order (most recent first)
    return jsonify({"items": list(reversed(seen)), "total": len(seen)})


# ---------------------------------------------------------------------------
# News Feedback — submit
# ---------------------------------------------------------------------------

@corpus_bp.post("/api/corpus/news-feedback")
@require_perm("corpus:news_watch")
def news_feedback_submit():
    """
    Submit a +1 or -1 rating for a news bullet.
    Body: {"bullet": "- [ ] 📰 ...", "feedback": "+1" | "-1"}
    """
    body = request.get_json(silent=True) or {}
    bullet = (body.get("bullet") or "").strip()
    feedback = (body.get("feedback") or "").strip()

    if not bullet:
        return jsonify({"error": "bullet is required"}), 400
    if feedback not in ("+1", "-1"):
        return jsonify({"error": "feedback must be '+1' or '-1'"}), 400

    updated = news_watch.submit_feedback(config.USER_DATA_ROOT, bullet, feedback)
    if not updated:
        return jsonify({"error": "Bullet not found in seen list"}), 404

    return jsonify({"updated": True, "feedback": feedback})
