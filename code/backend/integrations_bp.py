"""
Integrations Blueprint — manual trigger endpoints for Telegram, email, Jira.

Endpoints:
  POST /api/integrations/telegram        — send a Telegram message now
  POST /api/integrations/email           — send an email now
  POST /api/integrations/jira/issue      — create a Jira issue now
  POST /api/integrations/jira/sync       — enqueue a Jira corpus sync
  GET  /api/integrations/status          — which integrations are configured
"""

import logging

from flask import Blueprint, jsonify, request

import config
import task_queue
from auth_utils import require_perm

logger = logging.getLogger(__name__)

integrations_bp = Blueprint("integrations", __name__)


def _db():
    from app import get_db
    return get_db()


# ---------------------------------------------------------------------------
# Integration status
# ---------------------------------------------------------------------------

@integrations_bp.get("/api/integrations/status")
@require_perm("integrations:sync")
def integrations_status():
    """Return which integrations are currently configured (no secrets returned)."""
    return jsonify({
        "telegram": bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_DEFAULT_CHAT_ID),
        "email":    bool(config.O365_CLIENT_ID and config.O365_CLIENT_SECRET
                         and config.O365_TENANT_ID and config.O365_MAILBOX),
        "jira":     bool(config.JIRA_BASE_URL and config.JIRA_USER_EMAIL and config.JIRA_API_TOKEN),
    })


# ---------------------------------------------------------------------------
# Telegram — send now
# ---------------------------------------------------------------------------

@integrations_bp.post("/api/integrations/telegram")
@require_perm("integrations:send")
def send_telegram_now():
    """
    Send a Telegram message immediately (not via task queue).
    Body: {"message": "...", "chat_id": "..." (optional)}
    """
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    import integrations
    ok = integrations.send_telegram(message, chat_id=body.get("chat_id"))
    if ok:
        return jsonify({"sent": True})
    return jsonify({"error": "Failed to send — check TELEGRAM_BOT_TOKEN and TELEGRAM_DEFAULT_CHAT_ID"}), 502


# ---------------------------------------------------------------------------
# Email — enqueue (Graph API calls can be slow)
# ---------------------------------------------------------------------------

@integrations_bp.post("/api/integrations/email")
@require_perm("integrations:send")
def send_email_queued():
    """
    Enqueue an email task.
    Body: {"to": "...", "subject": "...", "body": "...", "body_html": "..." (optional)}
    """
    body = request.get_json(silent=True) or {}
    to = (body.get("to") or "").strip()
    subject = (body.get("subject") or "").strip()
    text = (body.get("body") or "").strip()
    if not to or not subject or not text:
        return jsonify({"error": "to, subject, and body are required"}), 400

    payload = {"to": to, "subject": subject, "body": text}
    if body.get("body_html"):
        payload["body_html"] = body["body_html"]

    db = _db()
    task_id = task_queue.enqueue(db, "email", payload)
    return jsonify({"task_id": task_id, "message": "Email queued"}), 202


# ---------------------------------------------------------------------------
# Jira — create issue (enqueued for retry safety)
# ---------------------------------------------------------------------------

@integrations_bp.post("/api/integrations/jira/issue")
@require_perm("integrations:send")
def jira_create_queued():
    """
    Enqueue a Jira issue creation task.
    Body: {"project_key": "...", "summary": "...", "description": "...", "issue_type": "Task"}
    """
    body = request.get_json(silent=True) or {}
    project_key = (body.get("project_key") or "").strip().upper()
    summary = (body.get("summary") or "").strip()
    if not project_key or not summary:
        return jsonify({"error": "project_key and summary are required"}), 400

    payload = {
        "project_key": project_key,
        "summary": summary,
        "description": (body.get("description") or ""),
        "issue_type": (body.get("issue_type") or "Task"),
    }
    db = _db()
    task_id = task_queue.enqueue(db, "jira_create", payload)
    return jsonify({"task_id": task_id, "message": "Jira issue creation queued"}), 202


# ---------------------------------------------------------------------------
# Jira — corpus sync
# ---------------------------------------------------------------------------

@integrations_bp.post("/api/integrations/jira/sync")
@require_perm("integrations:sync")
def jira_sync_now():
    """Enqueue an immediate Jira corpus sync."""
    db = _db()
    task_id = task_queue.enqueue(db, "jira_sync", {"user_nick": config.USER_NICK})
    return jsonify({"task_id": task_id, "message": "Jira sync queued"}), 202
