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

def test_healthz_no_auth(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_auth_me_with_bypass(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["role"] == "owner"
    assert "projects:read" in data["permissions"]
