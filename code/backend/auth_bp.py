"""
Auth routes — self-issued JWT login, no external identity provider.

Public API:
    POST /api/auth/login   -- {username, password} -> {token, role, username}
    POST /api/auth/logout  -- revokes all of the caller's tokens (token_version bump)
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


# login_events stores whatever an unauthenticated client sends — bound the
# two free-text fields so a flood of junk can't bloat the DB.
_MAX_USERNAME_LEN = 64
_MAX_USER_AGENT_LEN = 512


def _log_attempt(conn, username: str, success: bool):
    ip = request.remote_addr
    city, country = geoip_lookup.resolve(ip)
    conn.execute(
        """
        INSERT INTO login_events (username, success, ip_address, user_agent, geo_city, geo_country)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            username[:_MAX_USERNAME_LEN],
            1 if success else 0,
            ip,
            request.headers.get("User-Agent", "")[:_MAX_USER_AGENT_LEN],
            city,
            country,
        ),
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
    if len(username) > _MAX_USERNAME_LEN:
        return jsonify({"error": "Invalid credentials"}), 401

    conn = local_db.get_db()
    try:
        if auth_utils.is_locked_out(conn, username, request.remote_addr):
            logger.warning("login: %r locked out (too many recent failures)", username)
            return jsonify({"error": "Too many failed attempts. Try again later."}), 429

        row = conn.execute(
            "SELECT username, password_hash, role, token_version FROM users WHERE username = ?",
            (username,),
        ).fetchone()

        if not auth_utils.verify_login(row, password):
            _log_attempt(conn, username, False)
            return jsonify({"error": "Invalid credentials"}), 401

        _log_attempt(conn, username, True)
        token = auth_utils.issue_token(row["username"], row["role"], row["token_version"])
        return jsonify({"token": token, "role": row["role"], "username": row["username"]})
    finally:
        local_db.return_db(conn)


@auth_bp.post("/logout")
def logout():
    """
    Revoke every token issued to the current user (bumps token_version). With
    one or two personal accounts, "log out everywhere" is the right semantics
    — a stolen 7-day token dies the moment the owner signs out.
    """
    user = getattr(g, "user", None)
    if user and not config.DEV_AUTH_BYPASS:
        conn = local_db.get_db()
        try:
            auth_utils.revoke_all_tokens(conn, user["sub"])
        finally:
            local_db.return_db(conn)
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
