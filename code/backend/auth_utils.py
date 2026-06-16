import functools
import jwt
from flask import g, request, jsonify
import config
import config_rbac


# ---------------------------------------------------------------------------
# Dev bypass
# ---------------------------------------------------------------------------

def _synthetic_user():
    return {
        "sub": "dev-user",
        "name": "Dev User",
        "email": "dev@local",
        "roles": ["owner"],
        "role": "owner",
        "permissions": compute_permissions(["owner"]),
    }


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def compute_permissions(roles: list[str]) -> set[str]:
    """Return the union of all permission keys for the given roles."""
    perms = set()
    for perm_key, granted_roles in config_rbac.PERMISSIONS.items():
        if granted_roles == ("*",) or any(r in granted_roles for r in roles):
            perms.add(perm_key)
    return perms


def has_perm(perm_key: str) -> bool:
    user = getattr(g, "user", None)
    if not user:
        return False
    if user.get("role") == "owner":
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
# Keycloak token validation
# A before_request factory — call make_auth_handler() and register the result.
# ---------------------------------------------------------------------------

_jwks_client = None


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        jwks_url = _jwks_url()
        _jwks_client = jwt.PyJWKClient(jwks_url)
    return _jwks_client


def _jwks_url():
    base = config.KEYCLOAK_HOST_IP or config.KEYCLOAK_PUBLIC_URL
    realm = config.KEYCLOAK_REALM
    # Strip trailing slash
    base = base.rstrip("/")
    if config.KEYCLOAK_HOST_IP:
        # Internal rewrite: use http + LAN IP, port 8080
        base = f"http://{config.KEYCLOAK_HOST_IP}:8080"
    return f"{base}/realms/{realm}/protocol/openid-connect/certs"


def validate_token():
    """
    before_request handler. Populates g.user or aborts with 401.
    Skipped for /healthz and OPTIONS.
    """
    if request.method == "OPTIONS":
        return
    if request.path == "/healthz":
        return

    if config.DEV_AUTH_BYPASS:
        g.user = _synthetic_user()
        return

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Missing token"}), 401

    token = auth_header[len("Bearer "):]
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except Exception:
        # Evict cached JWKS client and retry once (handles key rotation)
        global _jwks_client
        _jwks_client = None
        try:
            signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
        except Exception:
            return jsonify({"error": "Invalid token"}), 401

    azp = payload.get("azp", "")
    if azp != config.KEYCLOAK_CLIENT_ID:
        return jsonify({"error": "Invalid client"}), 401

    realm_roles = payload.get("realm_access", {}).get("roles", [])
    client_roles = (
        payload.get("resource_access", {})
        .get(config.KEYCLOAK_CLIENT_ID, {})
        .get("roles", [])
    )
    all_roles = list(set(realm_roles + client_roles))

    # Map Keycloak roles → app roles
    app_roles = []
    if any(r in config_rbac.OWNER_REALM_ROLES for r in all_roles):
        app_roles.append("owner")

    primary_role = app_roles[0] if app_roles else "owner"

    g.user = {
        "sub": payload.get("sub"),
        "name": payload.get("name", ""),
        "email": payload.get("email", ""),
        "roles": app_roles,
        "role": primary_role,
        "permissions": compute_permissions(app_roles),
    }
