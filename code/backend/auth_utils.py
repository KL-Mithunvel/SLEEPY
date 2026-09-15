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

def issue_token(username: str, role: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + datetime.timedelta(days=config.AUTH_TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, config.AUTH_SECRET_KEY, algorithm="HS256")


# ---------------------------------------------------------------------------
# Brute-force lockout — reuses login_events, no separate attempts table
# ---------------------------------------------------------------------------

LOCKOUT_MAX_ATTEMPTS = 5
LOCKOUT_WINDOW_MINUTES = 15


def is_locked_out(conn, username: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM login_events
        WHERE username = ? AND success = 0
          AND created_at >= datetime('now', 'localtime', ?)
        """,
        (username, f"-{LOCKOUT_WINDOW_MINUTES} minutes"),
    ).fetchone()
    return row["n"] >= LOCKOUT_MAX_ATTEMPTS


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

    g.user = {
        "sub": username,
        "name": username,
        "email": "",
        "roles": [role],
        "role": role,
        "permissions": compute_permissions([role]),
    }
