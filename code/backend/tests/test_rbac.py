"""
Layer 1 — RBAC unit tests (no HTTP, no DB).
These run first. If they fail, nothing else should be trusted.
"""

import pytest
import config_rbac
from auth_utils import compute_permissions, has_perm, require_perm


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

def test_owner_gets_all_permissions():
    perms = compute_permissions(["owner"])
    for key in config_rbac.PERMISSIONS:
        assert key in perms, f"owner missing permission: {key!r}"


def test_empty_roles_get_no_permissions():
    perms = compute_permissions([])
    # Wildcard permissions are still granted
    wildcard_keys = {k for k, v in config_rbac.PERMISSIONS.items() if v == ("*",)}
    non_wildcard = set(config_rbac.PERMISSIONS) - wildcard_keys
    for key in non_wildcard:
        assert key not in perms


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
    assert data["role"] == "owner"
    assert "projects:read" in data["permissions"]


# ---------------------------------------------------------------------------
# validate_token() with DEV_AUTH_BYPASS=0 — the real Keycloak path.
# S1 regression: a validly-signed token with no OWNER_REALM_ROLES must NOT
# silently become owner. Covers the exact gap the security review flagged
# (138 green tests previously existed with zero coverage of this branch).
# ---------------------------------------------------------------------------

class _FakeSigningKey:
    key = "fake-key"


def _mock_jwt_stack(monkeypatch, payload):
    import auth_utils
    monkeypatch.setattr(auth_utils, "_get_jwks_client", lambda: type(
        "FakeJwks", (), {"get_signing_key_from_jwt": staticmethod(lambda t: _FakeSigningKey())}
    )())
    import jwt as jwt_module
    monkeypatch.setattr(jwt_module, "decode", lambda *a, **k: payload)


def test_token_with_no_app_role_is_rejected(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "KEYCLOAK_CLIENT_ID", "pma")
    _mock_jwt_stack(monkeypatch, {
        "azp": "pma", "sub": "u1", "realm_access": {"roles": ["some-other-realm-role"]},
        "resource_access": {},
    })

    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer faketoken"})
    assert resp.status_code == 403


def test_token_with_owner_realm_role_is_accepted(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "KEYCLOAK_CLIENT_ID", "pma")
    _mock_jwt_stack(monkeypatch, {
        "azp": "pma", "sub": "u1", "name": "Real User", "email": "u1@smtw.in",
        "realm_access": {"roles": ["owner"]}, "resource_access": {},
    })

    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer faketoken"})
    assert resp.status_code == 200
    assert resp.get_json()["role"] == "owner"


def test_missing_token_rejected_without_bypass(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_auth_config_accessible_without_token(client, monkeypatch):
    """S3 regression: the frontend must fetch this before it has a token to
    initialise Keycloak at all — requiring auth here deadlocks login in prod."""
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    resp = client.get("/api/auth/config")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "clientId" in data


def test_wrong_client_id_rejected(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "KEYCLOAK_CLIENT_ID", "pma")
    _mock_jwt_stack(monkeypatch, {
        "azp": "some-other-client", "sub": "u1",
        "realm_access": {"roles": ["owner"]}, "resource_access": {},
    })

    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer faketoken"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# S7 regression: issuer verification with a REAL RSA-signed token (not a
# mocked jwt.decode) — proves PyJWT's own issuer check is actually wired in,
# not just that the right kwarg is passed.
# ---------------------------------------------------------------------------

def _make_rsa_keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = key.public_key()
    return private_pem, public_key


def _mock_real_jwks(monkeypatch, public_key):
    import auth_utils
    monkeypatch.setattr(auth_utils, "_get_jwks_client", lambda: type(
        "FakeJwks", (), {"get_signing_key_from_jwt": staticmethod(lambda t: type(
            "FakeSigningKey", (), {"key": public_key}
        )())}
    )())


def test_real_token_wrong_issuer_rejected(client, monkeypatch):
    import config
    import jwt as real_jwt

    private_pem, public_key = _make_rsa_keypair()
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "KEYCLOAK_CLIENT_ID", "pma")
    monkeypatch.setattr(config, "KEYCLOAK_PUBLIC_URL", "https://auth.office.smtw.in")
    monkeypatch.setattr(config, "KEYCLOAK_REALM", "Office.smtw.in")
    _mock_real_jwks(monkeypatch, public_key)

    token = real_jwt.encode(
        {
            "azp": "pma", "sub": "u1", "iss": "https://evil.example.com/realms/fake",
            "realm_access": {"roles": ["owner"]},
        },
        private_pem, algorithm="RS256",
    )

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_real_token_correct_issuer_accepted(client, monkeypatch):
    import config
    import jwt as real_jwt

    private_pem, public_key = _make_rsa_keypair()
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "KEYCLOAK_CLIENT_ID", "pma")
    monkeypatch.setattr(config, "KEYCLOAK_PUBLIC_URL", "https://auth.office.smtw.in")
    monkeypatch.setattr(config, "KEYCLOAK_REALM", "Office.smtw.in")
    _mock_real_jwks(monkeypatch, public_key)

    token = real_jwt.encode(
        {
            "azp": "pma", "sub": "u1", "iss": "https://auth.office.smtw.in/realms/Office.smtw.in",
            "realm_access": {"roles": ["owner"]},
        },
        private_pem, algorithm="RS256",
    )

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
