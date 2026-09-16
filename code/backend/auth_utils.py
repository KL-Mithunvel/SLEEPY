import datetime
import functools
import logging
import uuid

import jwt
from flask import g, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

import config
import config_rbac

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dev bypass
# ---------------------------------------------------------------------------

def _synthetic_user():
    return {
        "sub": "dev-user",
        "name": "Dev User",
        "email": "dev@local",
        "roles": ["admin"],
        "role": "admin",
        "permissions": compute_permissions(["admin"]),
    }


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def compute_permissions(roles: list[str]) -> set[str]:
    """
    Return the union of all permission keys for the given roles.
    "admin" is expanded to every declared key rather than looked up in the
    table — PERMISSIONS never lists "admin" as a grantee (see
    test_no_admin_in_permission_tuples), so this is what makes an admin's
    own /api/auth/me response (and therefore the frontend's nav-item
    visibility, which checks that list directly) actually complete.
    """
    if "admin" in roles:
        return set(config_rbac.PERMISSIONS.keys())
    perms = set()
    for perm_key, granted_roles in config_rbac.PERMISSIONS.items():
        if granted_roles == ("*",) or any(r in granted_roles for r in roles):
            perms.add(perm_key)
    return perms


def has_perm(perm_key: str) -> bool:
    user = getattr(g, "user", None)
    if not user:
        return False
    if user.get("role") == "admin":
        return True
    return perm_key in user.get("permissions", set())


def require_perm(perm_key: str):
    """Decorator — gates a route; returns 403 if the user lacks the permission."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not has_perm(perm_key):
                return jsonify({"error": "Forbidden"}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)


# ---------------------------------------------------------------------------
# Self-issued JWT (HS256) — no external identity provider
# ---------------------------------------------------------------------------

def issue_token(username: str, role: str, token_version: int = 0) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "ver": int(token_version),
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + datetime.timedelta(days=config.AUTH_TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, config.AUTH_SECRET_KEY, algorithm="HS256")


def revoke_all_tokens(conn, username: str) -> None:
    """
    Invalidate every token ever issued to `username` by bumping their
    token_version — tokens carry the version they were minted with and
    validate_token rejects any that no longer match. Used by logout and
    password reset (stateless JWTs can't otherwise be recalled).
    """
    conn.execute(
        "UPDATE users SET token_version = token_version + 1 WHERE username = ?",
        (username,),
    )
    conn.commit()


# A real scrypt hash of a throwaway password, verified on the unknown-username
# path so a miss costs the same time as a wrong password — otherwise response
# timing reveals which usernames exist.
_DUMMY_HASH = generate_password_hash("sleepy-dummy-password-for-timing")


def verify_login(row, password: str) -> bool:
    """Constant-effort credential check: always runs exactly one hash verify."""
    if row is None:
        check_password_hash(_DUMMY_HASH, password)
        return False
    return check_password_hash(row["password_hash"], password)


# ---------------------------------------------------------------------------
# Brute-force lockout — reuses login_events, no separate attempts table
# ---------------------------------------------------------------------------

LOCKOUT_MAX_ATTEMPTS = 5          # per (username, client IP) pair
LOCKOUT_IP_MAX_ATTEMPTS = 20      # per client IP across all usernames
LOCKOUT_WINDOW_MINUTES = 15


def is_locked_out(conn, username: str, ip_address: str | None = None) -> bool:
    """
    Locked out when, within the window, either:
      - this (username, ip) pair has LOCKOUT_MAX_ATTEMPTS failures, or
      - this ip has LOCKOUT_IP_MAX_ATTEMPTS failures against any usernames.

    Keyed on the pair rather than the bare username so an outsider who knows
    the username can't lock the real owner out from a different address
    just by sending five wrong passwords every fifteen minutes.
    """
    window = f"-{LOCKOUT_WINDOW_MINUTES} minutes"
    ip = ip_address or ""
    pair = conn.execute(
        """
        SELECT COUNT(*) AS n FROM login_events
        WHERE username = ? AND success = 0
          AND COALESCE(ip_address, '') = ?
          AND created_at >= datetime('now', 'localtime', ?)
        """,
        (username, ip, window),
    ).fetchone()
    if pair["n"] >= LOCKOUT_MAX_ATTEMPTS:
        return True
    if not ip:
        return False
    by_ip = conn.execute(
        """
        SELECT COUNT(*) AS n FROM login_events
        WHERE success = 0 AND ip_address = ?
          AND created_at >= datetime('now', 'localtime', ?)
        """,
        (ip, window),
    ).fetchone()
    return by_ip["n"] >= LOCKOUT_IP_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# Request auth — before_request handler
# ---------------------------------------------------------------------------

_PUBLIC_PATHS = {"/healthz", "/api/auth/config", "/api/auth/login"}


def validate_token():
    """
    before_request handler. Populates g.user or aborts with 401.
    Skipped for OPTIONS and the public bootstrap/login paths.
    """
    if request.method == "OPTIONS":
        return
    if request.path in _PUBLIC_PATHS:
        return

    if config.DEV_AUTH_BYPASS:
        g.user = _synthetic_user()
        return

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Missing token"}), 401

    token = auth_header[len("Bearer "):]
    try:
        payload = jwt.decode(token, config.AUTH_SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return jsonify({"error": "Token expired"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"error": "Invalid token"}), 401

    role = payload.get("role")
    username = payload.get("sub")
    if role not in config_rbac.ROLES or not username:
        return jsonify({"error": "Invalid token"}), 401

    # Revocation check — the token's version must still match the user's
    # current one (logout / password reset bump it), and the account must
    # still exist. Role is taken from the DB, not the token, so a role change
    # takes effect without waiting for the old token to expire.
    import local_db
    conn = local_db.get_db()
    try:
        row = conn.execute(
            "SELECT role, token_version FROM users WHERE username = ?", (username,)
        ).fetchone()
    finally:
        local_db.return_db(conn)
    if row is None or int(payload.get("ver", -1)) != int(row["token_version"]):
        return jsonify({"error": "Token revoked"}), 401
    role = row["role"]
    if role not in config_rbac.ROLES:
        return jsonify({"error": "Invalid token"}), 401

    g.user = {
        "sub": username,
        "name": username,
        "email": "",
        "roles": [role],
        "role": role,
        "permissions": compute_permissions([role]),
    }
