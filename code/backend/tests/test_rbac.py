"""
Layer 1 — RBAC + in-app auth unit tests (no HTTP except where noted).
These run first. If they fail, nothing else should be trusted.
"""

import datetime

import jwt
import pytest

import config_rbac
from auth_utils import (
    compute_permissions,
    hash_password,
    verify_password,
    issue_token,
    is_locked_out,
)


# ---------------------------------------------------------------------------
# config_rbac structure
# ---------------------------------------------------------------------------

def test_roles_tuple_ordered():
    assert isinstance(config_rbac.ROLES, tuple)
    assert len(config_rbac.ROLES) >= 1


def test_permission_keys_have_colons():
    for key in config_rbac.PERMISSIONS:
        assert ":" in key, f"Permission key missing colon: {key!r}"


def test_no_admin_in_permission_tuples():
    # "admin" bypass is handled in code, never in the policy table
    for key, roles in config_rbac.PERMISSIONS.items():
        assert "admin" not in roles, f"{key!r} grants 'admin' — use code bypass instead"


def test_wildcards_not_mixed():
    for key, roles in config_rbac.PERMISSIONS.items():
        if ("*",) == roles:
            continue
        assert "*" not in roles, f"{key!r} mixes '*' with other roles"


def test_all_granted_roles_are_known():
    known = set(config_rbac.ROLES)
    for key, roles in config_rbac.PERMISSIONS.items():
        if roles == ("*",):
            continue
        for role in roles:
            assert role in known, f"{key!r} grants unknown role {role!r}"


# ---------------------------------------------------------------------------
# compute_permissions
# ---------------------------------------------------------------------------

def test_admin_gets_all_permissions():
    perms = compute_permissions(["admin"])
    for key in config_rbac.PERMISSIONS:
        assert key in perms, f"admin missing permission: {key!r}"


def test_user_does_not_get_admin_security():
    perms = compute_permissions(["user"])
    assert "admin:security" not in perms


def test_user_gets_normal_app_permissions():
    perms = compute_permissions(["user"])
    assert "projects:read" in perms
    assert "ai:suggest" in perms


def test_empty_roles_get_no_permissions():
    perms = compute_permissions([])
    wildcard_keys = {k for k, v in config_rbac.PERMISSIONS.items() if v == ("*",)}
    non_wildcard = set(config_rbac.PERMISSIONS) - wildcard_keys
    for key in non_wildcard:
        assert key not in perms


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def test_password_hash_roundtrip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True


def test_password_hash_rejects_wrong_password():
    h = hash_password("correct horse battery staple")
    assert verify_password("wrong password", h) is False


def test_password_hash_is_not_plaintext():
    h = hash_password("hunter2")
    assert h != "hunter2"


# ---------------------------------------------------------------------------
# Self-issued JWT (HS256)
# ---------------------------------------------------------------------------

def test_issue_token_roundtrip(monkeypatch):
    import config
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret")
    token = issue_token("klm", "user")
    payload = jwt.decode(token, "test-secret", algorithms=["HS256"])
    assert payload["sub"] == "klm"
    assert payload["role"] == "user"
    assert "jti" in payload


def test_issue_token_rejects_with_wrong_secret(monkeypatch):
    import config
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret")
    token = issue_token("klm", "user")
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, "a-different-secret", algorithms=["HS256"])


def test_expired_token_rejected(monkeypatch):
    import config
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret")
    now = datetime.datetime.now(datetime.timezone.utc)
    expired_payload = {
        "sub": "klm", "role": "user", "jti": "x",
        "iat": now - datetime.timedelta(days=8),
        "exp": now - datetime.timedelta(days=1),
    }
    token = jwt.encode(expired_payload, "test-secret", algorithm="HS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(token, "test-secret", algorithms=["HS256"])


# ---------------------------------------------------------------------------
# HTTP enforcement (requires app context)
# ---------------------------------------------------------------------------

def test_oversized_request_body_rejected(client):
    """S8 regression: unbounded POST bodies must not reach the LLM/handlers."""
    oversized = "x" * (3 * 1024 * 1024)  # 3 MB > the 2 MB app.config limit
    resp = client.put("/api/projects/content", json={"path": "SMTW/x.md", "content": oversized})
    assert resp.status_code == 413


def test_healthz_no_auth(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_auth_me_with_bypass(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["role"] == "admin"
    assert "projects:read" in data["permissions"]
    assert "admin:security" in data["permissions"]
    assert "admin:ai_usage" in data["permissions"]


def test_missing_token_rejected_without_bypass(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_auth_config_accessible_without_token(client, monkeypatch):
    """The frontend must fetch this before it has a token at all."""
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    resp = client.get("/api/auth/config")
    assert resp.status_code == 200
    assert "devBypass" in resp.get_json()


def test_real_token_accepted(client, monkeypatch, create_user):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret")
    create_user("klm", role="user")

    from auth_utils import issue_token
    token = issue_token("klm", "user")

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["sub"] == "klm"
    assert data["role"] == "user"


def test_tampered_token_rejected(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret")

    from auth_utils import issue_token
    token = issue_token("klm", "user") + "tampered"

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_token_signed_with_wrong_secret_rejected(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "the-real-secret")

    now = datetime.datetime.now(datetime.timezone.utc)
    forged = jwt.encode(
        {"sub": "klm", "role": "admin", "jti": "x", "iat": now,
         "exp": now + datetime.timedelta(days=1)},
        "attacker-guessed-secret", algorithm="HS256",
    )

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


def test_token_with_unknown_role_rejected(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret")

    now = datetime.datetime.now(datetime.timezone.utc)
    token = jwt.encode(
        {"sub": "klm", "role": "superuser", "jti": "x", "iat": now,
         "exp": now + datetime.timedelta(days=1)},
        "test-secret", algorithm="HS256",
    )

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Lockout
# ---------------------------------------------------------------------------

def test_is_locked_out_false_when_no_failures(app):
    import local_db
    conn = local_db.get_db()
    try:
        assert is_locked_out(conn, "nobody-has-tried-this-user") is False
    finally:
        local_db.return_db(conn)


def test_is_locked_out_true_after_threshold(app):
    import local_db
    from auth_utils import LOCKOUT_MAX_ATTEMPTS

    conn = local_db.get_db()
    try:
        for _ in range(LOCKOUT_MAX_ATTEMPTS):
            conn.execute(
                "INSERT INTO login_events (username, success) VALUES (?, 0)",
                ("lockout-test-user",),
            )
        conn.commit()
        assert is_locked_out(conn, "lockout-test-user") is True
    finally:
        local_db.return_db(conn)
