"""
Auth routes — self-issued JWT login, no external identity provider.

Public API:
    POST /api/auth/login   -- {username, password} -> {token, role, username}
    POST /api/auth/logout  -- stateless JWT, nothing to revoke server-side;
                               kept for symmetry / future-proofing
    GET  /api/auth/me      -- current user's identity + permissions
    GET  /api/auth/config  -- public bootstrap info (devBypass flag only)
"""

import logging

from flask import Blueprint, g, jsonify, request

import auth_utils
import config
import geoip_lookup
import local_db

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth_bp", __name__, url_prefix="/api/auth")


def _log_attempt(conn, username: str, success: bool):
    ip = request.remote_addr
    city, country = geoip_lookup.resolve(ip)
    conn.execute(
        """
        INSERT INTO login_events (username, success, ip_address, user_agent, geo_city, geo_country)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (username, 1 if success else 0, ip, request.headers.get("User-Agent", ""), city, country),
    )
    conn.commit()


@auth_bp.get("/config")
def auth_config():
    return jsonify({"devBypass": config.DEV_AUTH_BYPASS})


@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    conn = local_db.get_db()
    try:
        if auth_utils.is_locked_out(conn, username):
            logger.warning("login: %r locked out (too many recent failures)", username)
            return jsonify({"error": "Too many failed attempts. Try again later."}), 429

        row = conn.execute(
            "SELECT username, password_hash, role FROM users WHERE username = ?", (username,)
        ).fetchone()

        if row is None or not auth_utils.verify_password(password, row["password_hash"]):
            _log_attempt(conn, username, False)
            return jsonify({"error": "Invalid credentials"}), 401

        _log_attempt(conn, username, True)
        token = auth_utils.issue_token(row["username"], row["role"])
        return jsonify({"token": token, "role": row["role"], "username": row["username"]})
    finally:
        local_db.return_db(conn)


@auth_bp.post("/logout")
def logout():
    return jsonify({"status": "ok"})


@auth_bp.get("/me")
def me():
    user = g.user
    return jsonify({
        "sub": user["sub"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "permissions": list(user["permissions"]),
    })
