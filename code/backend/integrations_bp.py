"""
Integrations Blueprint — manual trigger endpoints for email.

Endpoints:
  POST /api/integrations/email           — send an email now
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
        "email": bool(config.O365_CLIENT_ID and config.O365_CLIENT_SECRET
                     and config.O365_TENANT_ID and config.O365_MAILBOX),
    })


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
