"""
Admin-only security monitoring — visibility into login attempts (who, when,
from where, success/fail), gated by the admin:security permission.

Public API:
    GET /api/admin/login-events  -- paginated, most recent first
"""

from flask import Blueprint, jsonify, request

import auth_utils
import local_db
from db_helpers import rows_to_list

admin_bp = Blueprint("admin_bp", __name__, url_prefix="/api/admin")


@admin_bp.get("/login-events")
@auth_utils.require_perm("admin:security")
def login_events():
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        return jsonify({"error": "limit and offset must be integers"}), 400

    conn = local_db.get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, username, success, ip_address, user_agent, geo_city, geo_country, created_at
            FROM login_events
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM login_events").fetchone()["n"]
        return jsonify({"events": rows_to_list(rows), "total": total})
    finally:
        local_db.return_db(conn)
