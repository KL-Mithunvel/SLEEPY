"""
Admin-only monitoring — login attempts and AI usage, gated by
admin:security / admin:ai_usage respectively.

Public API:
    GET /api/admin/login-events  -- paginated, most recent first
    GET /api/admin/ai-usage      -- token/call totals by model + event type,
                                     a daily trend, and a paginated recent list
"""

from flask import Blueprint, jsonify, request

import auth_utils
import local_db
from db_helpers import row_to_dict, rows_to_list

admin_bp = Blueprint("admin_bp", __name__, url_prefix="/api/admin")

# ai_events rows written by md_edit/md_move/md_delete are corpus-write
# proposals, not LLM calls — they carry no model/token data. Scoping every
# usage query to model IS NOT NULL excludes them automatically. voided rows
# are excluded too, same convention as the morning-briefing lookup in
# today_bp.py (an immutable log entry logically cancelled, never deleted).
_USAGE_WHERE = "model IS NOT NULL AND voided = 0"


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


@admin_bp.get("/ai-usage")
@auth_utils.require_perm("admin:ai_usage")
def ai_usage():
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        return jsonify({"error": "limit and offset must be integers"}), 400

    conn = local_db.get_db()
    try:
        summary = conn.execute(
            f"""
            SELECT COUNT(*) AS total_calls,
                   COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
                   AVG(latency_ms) AS avg_latency_ms
            FROM ai_events WHERE {_USAGE_WHERE}
            """
        ).fetchone()

        by_model = conn.execute(
            f"""
            SELECT model,
                   COUNT(*) AS calls,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens
            FROM ai_events WHERE {_USAGE_WHERE}
            GROUP BY model ORDER BY calls DESC
            """
        ).fetchall()

        by_type = conn.execute(
            f"""
            SELECT event_type,
                   COUNT(*) AS calls,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens
            FROM ai_events WHERE {_USAGE_WHERE}
            GROUP BY event_type ORDER BY calls DESC
            """
        ).fetchall()

        daily = conn.execute(
            f"""
            SELECT substr(created_at, 1, 10) AS day,
                   COUNT(*) AS calls,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens
            FROM ai_events WHERE {_USAGE_WHERE}
            GROUP BY day ORDER BY day DESC LIMIT 30
            """
        ).fetchall()

        recent = conn.execute(
            f"""
            SELECT id, event_type, model, input_tokens, output_tokens, latency_ms, accepted, created_at
            FROM ai_events WHERE {_USAGE_WHERE}
            ORDER BY id DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

        total = conn.execute(f"SELECT COUNT(*) AS n FROM ai_events WHERE {_USAGE_WHERE}").fetchone()["n"]

        return jsonify({
            "summary": row_to_dict(summary),
            "by_model": rows_to_list(by_model),
            "by_type": rows_to_list(by_type),
            "daily": rows_to_list(daily),
            "recent": rows_to_list(recent),
            "total": total,
        })
    finally:
        local_db.return_db(conn)
