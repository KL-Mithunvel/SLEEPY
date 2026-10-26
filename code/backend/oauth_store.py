"""
OAuth 2.1 storage + rules for the MCP connector — no Flask in here.

The connector (Claude mobile, ...) never sees the user's password or a session
JWT. It registers itself, sends the user to the consent page, and ends up
holding two opaque random strings: a short-lived access token and a rotating
refresh token. Only their sha256 is stored, so a leaked DB yields nothing
usable, and every credential can be revoked by flipping a row.

    register_client(conn, name, redirect_uris)        -> client dict
    get_client(conn, client_id)                       -> client dict | None
    redirect_uri_allowed(uri)                         -> bool
    create_code(conn, ...)                            -> raw code
    redeem_code(conn, ...)                            -> token response dict   (raises OAuthError)
    refresh(conn, ...)                                -> token response dict   (raises OAuthError)
    authenticate_access(conn, raw_token)              -> {username, scopes, client_id} | None
    revoke_by_token(conn, raw_token)                  -> None   (RFC 7009 — silent if unknown)
    revoke_all_for_user(conn, username)               -> number of tokens revoked
    list_connectors(conn)                             -> active grants, for the CLI

Reuse of a spent authorization code or a spent refresh token is treated as theft:
the whole grant family is revoked (OAuth 2.1 §4.3.1 / Security BCP §4.14).
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import config

SUPPORTED_SCOPES = ("tasks:read", "tasks:write")
_FMT = "%Y-%m-%d %H:%M:%S"
_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_\-]{43,128}$")
_VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_NAME_LEN = 100
_MAX_REDIRECTS = 5


class OAuthError(Exception):
    """RFC 6749 error — `code` is the wire error code, `description` is safe to show."""

    def __init__(self, code: str, description: str = ""):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now()


def _stamp(dt: datetime) -> str:
    return dt.strftime(_FMT)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_secret(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(32)}"


def normalise_scopes(requested: str | None, *, allow_default: bool = True) -> list[str]:
    """Space-delimited scope string -> validated list; raises invalid_scope for unknown ones."""
    parts = (requested or "").split()
    if not parts:
        if allow_default:
            return list(SUPPORTED_SCOPES)
        raise OAuthError("invalid_scope", "No scope requested")
    unknown = [p for p in parts if p not in SUPPORTED_SCOPES]
    if unknown:
        raise OAuthError("invalid_scope", f"Unsupported scope: {unknown[0][:40]}")
    return [s for s in SUPPORTED_SCOPES if s in parts]


# ---------------------------------------------------------------------------
# Clients (dynamic registration, RFC 7591)
# ---------------------------------------------------------------------------

def redirect_uri_allowed(uri: str) -> bool:
    if not isinstance(uri, str) or len(uri) > 500 or _CTRL_RE.search(uri):
        return False
    if uri in config.OAUTH_ALLOWED_REDIRECT_URIS:
        return True
    if config.OAUTH_ALLOW_LOOPBACK_REDIRECTS:
        parts = urlsplit(uri)
        return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1") and not parts.fragment
    return False


def register_client(conn, name, redirect_uris) -> dict:
    if not isinstance(redirect_uris, list) or not redirect_uris or len(redirect_uris) > _MAX_REDIRECTS:
        raise OAuthError("invalid_redirect_uri", "redirect_uris must be a non-empty list")
    for uri in redirect_uris:
        if not redirect_uri_allowed(uri):
            raise OAuthError("invalid_redirect_uri", "That redirect URI is not permitted by this server")

    name = _CTRL_RE.sub("", str(name or "")).strip()[:_MAX_NAME_LEN] or "Unnamed app"

    purge_expired(conn)
    total = conn.execute("SELECT COUNT(*) AS n FROM oauth_clients").fetchone()["n"]
    if total >= config.OAUTH_MAX_CLIENTS:
        # Make room by dropping registrations nobody ever used — never a client with a live grant.
        conn.execute(
            """
            DELETE FROM oauth_clients
            WHERE client_id NOT IN (SELECT client_id FROM oauth_tokens)
              AND client_id NOT IN (SELECT client_id FROM oauth_codes)
              AND created_at < datetime('now', 'localtime', '-1 day')
            """
        )
        total = conn.execute("SELECT COUNT(*) AS n FROM oauth_clients").fetchone()["n"]
        if total >= config.OAUTH_MAX_CLIENTS:
            conn.commit()
            raise OAuthError("temporarily_unavailable", "Too many registered clients; try again later")

    client_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO oauth_clients (client_id, client_name, redirect_uris) VALUES (?, ?, ?)",
        (client_id, name, json.dumps(redirect_uris)),
    )
    conn.commit()
    return {"client_id": client_id, "client_name": name, "redirect_uris": list(redirect_uris)}


def get_client(conn, client_id) -> dict | None:
    if not isinstance(client_id, str) or not client_id:
        return None
    row = conn.execute(
        "SELECT client_id, client_name, redirect_uris FROM oauth_clients WHERE client_id = ?",
        (client_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "client_id": row["client_id"],
        "client_name": row["client_name"],
        "redirect_uris": json.loads(row["redirect_uris"]),
    }


# ---------------------------------------------------------------------------
# Authorization codes
# ---------------------------------------------------------------------------

def valid_code_challenge(challenge) -> bool:
    return isinstance(challenge, str) and bool(_CHALLENGE_RE.match(challenge))


def create_code(conn, *, client_id: str, username: str, redirect_uri: str,
                code_challenge: str, scopes: list[str]) -> str:
    raw = _new_secret("slc_")
    now = _now()
    conn.execute(
        """
        INSERT INTO oauth_codes
            (code_hash, client_id, username, redirect_uri, code_challenge, scope,
             family_id, auth_time, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (_hash(raw), client_id, username, redirect_uri, code_challenge, " ".join(scopes),
         uuid.uuid4().hex, _stamp(now), _stamp(now + timedelta(seconds=config.OAUTH_CODE_TTL_SECONDS))),
    )
    conn.commit()
    return raw


def _pkce_ok(verifier: str, challenge: str) -> bool:
    if not isinstance(verifier, str) or not _VERIFIER_RE.match(verifier):
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(computed, challenge)


def _revoke_family(conn, family_id: str) -> None:
    conn.execute("UPDATE oauth_tokens SET revoked = 1 WHERE family_id = ?", (family_id,))


def redeem_code(conn, *, code: str, client_id: str, redirect_uri: str, code_verifier: str) -> dict:
    if not code or not isinstance(code, str):
        raise OAuthError("invalid_request", "code is required")
    row = conn.execute("SELECT * FROM oauth_codes WHERE code_hash = ?", (_hash(code),)).fetchone()
    if row is None:
        raise OAuthError("invalid_grant", "Invalid or expired code")

    if row["used"]:
        # A second redemption means someone else holds the code: kill what it produced.
        _revoke_family(conn, row["family_id"])
        conn.commit()
        raise OAuthError("invalid_grant", "Invalid or expired code")
    if row["expires_at"] < _stamp(_now()):
        raise OAuthError("invalid_grant", "Invalid or expired code")
    if row["client_id"] != client_id or row["redirect_uri"] != redirect_uri:
        raise OAuthError("invalid_grant", "Invalid or expired code")
    if not _pkce_ok(code_verifier, row["code_challenge"]):
        raise OAuthError("invalid_grant", "PKCE verification failed")
    if _user_missing(conn, row["username"]):
        raise OAuthError("invalid_grant", "Invalid or expired code")

    spent = conn.execute("UPDATE oauth_codes SET used = 1 WHERE code_hash = ? AND used = 0", (row["code_hash"],))
    if spent.rowcount != 1:  # lost a race with a concurrent redemption
        _revoke_family(conn, row["family_id"])
        conn.commit()
        raise OAuthError("invalid_grant", "Invalid or expired code")

    response = _issue_pair(conn, family_id=row["family_id"], client_id=client_id,
                           username=row["username"], scope=row["scope"], auth_time=row["auth_time"])
    conn.commit()
    return response


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def _user_missing(conn, username: str) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is None


def _grant_expired(auth_time: str) -> bool:
    started = datetime.strptime(auth_time, _FMT)
    return _now() > started + timedelta(days=config.OAUTH_GRANT_MAX_DAYS)


def _issue_pair(conn, *, family_id, client_id, username, scope, auth_time) -> dict:
    now = _now()
    access, refresh_raw = _new_secret("sla_"), _new_secret("slr_")
    access_ttl = timedelta(minutes=config.OAUTH_ACCESS_TTL_MINUTES)
    refresh_ttl = timedelta(days=config.OAUTH_REFRESH_TTL_DAYS)
    for raw, kind, ttl in ((access, "access", access_ttl), (refresh_raw, "refresh", refresh_ttl)):
        conn.execute(
            """
            INSERT INTO oauth_tokens
                (token_hash, kind, family_id, client_id, username, scope, auth_time, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_hash(raw), kind, family_id, client_id, username, scope, auth_time, _stamp(now + ttl)),
        )
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": int(access_ttl.total_seconds()),
        "refresh_token": refresh_raw,
        "scope": scope,
    }


def refresh(conn, *, refresh_token: str, client_id: str, scope: str | None = None) -> dict:
    if not refresh_token or not isinstance(refresh_token, str):
        raise OAuthError("invalid_request", "refresh_token is required")
    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE token_hash = ? AND kind = 'refresh'", (_hash(refresh_token),)
    ).fetchone()
    if row is None or row["client_id"] != client_id or row["revoked"]:
        raise OAuthError("invalid_grant", "Invalid refresh token")

    if row["used"]:
        # Already rotated: whoever is presenting it now is not the party that was given the new one.
        _revoke_family(conn, row["family_id"])
        conn.commit()
        raise OAuthError("invalid_grant", "Invalid refresh token")
    if row["expires_at"] < _stamp(_now()) or _grant_expired(row["auth_time"]) or _user_missing(conn, row["username"]):
        raise OAuthError("invalid_grant", "Invalid refresh token")

    granted = row["scope"].split()
    if scope:
        asked = normalise_scopes(scope, allow_default=False)
        if not set(asked) <= set(granted):
            raise OAuthError("invalid_scope", "Cannot widen scope on refresh")
        granted = asked

    spent = conn.execute("UPDATE oauth_tokens SET used = 1 WHERE id = ? AND used = 0", (row["id"],))
    if spent.rowcount != 1:
        _revoke_family(conn, row["family_id"])
        conn.commit()
        raise OAuthError("invalid_grant", "Invalid refresh token")

    response = _issue_pair(conn, family_id=row["family_id"], client_id=client_id, username=row["username"],
                           scope=" ".join(granted), auth_time=row["auth_time"])
    conn.commit()
    return response


def authenticate_access(conn, raw_token) -> dict | None:
    """Resolve a presented bearer string to a live grant, or None. Never raises."""
    if not isinstance(raw_token, str) or not raw_token.startswith("sla_") or len(raw_token) > 200:
        return None
    row = conn.execute(
        """
        SELECT t.id, t.username, t.scope, t.client_id, t.expires_at, t.auth_time, t.last_used_at
        FROM oauth_tokens t JOIN users u ON u.username = t.username
        WHERE t.token_hash = ? AND t.kind = 'access' AND t.revoked = 0
        """,
        (_hash(raw_token),),
    ).fetchone()
    if row is None:
        return None
    now = _stamp(_now())
    if row["expires_at"] < now or _grant_expired(row["auth_time"]):
        return None
    # Last-used is for the user's own audit ("is this connector still alive?"); a
    # write per call would be noise, so refresh it at most once a minute.
    if row["last_used_at"] is None or row["last_used_at"] < _stamp(_now() - timedelta(seconds=60)):
        conn.execute("UPDATE oauth_tokens SET last_used_at = ? WHERE id = ?", (now, row["id"]))
        conn.commit()
    return {"username": row["username"], "client_id": row["client_id"],
            "scopes": frozenset(s for s in row["scope"].split() if s in SUPPORTED_SCOPES)}


# ---------------------------------------------------------------------------
# Revocation + housekeeping
# ---------------------------------------------------------------------------

def revoke_by_token(conn, raw_token) -> None:
    """RFC 7009: revoke the whole grant the presented token belongs to; silent if unknown."""
    if not isinstance(raw_token, str) or not raw_token:
        return
    row = conn.execute("SELECT family_id FROM oauth_tokens WHERE token_hash = ?", (_hash(raw_token),)).fetchone()
    if row is not None:
        _revoke_family(conn, row["family_id"])
        conn.commit()


def revoke_all_for_user(conn, username: str) -> int:
    cur = conn.execute("UPDATE oauth_tokens SET revoked = 1 WHERE username = ? AND revoked = 0", (username,))
    conn.execute("UPDATE oauth_codes SET used = 1 WHERE username = ?", (username,))
    conn.commit()
    return cur.rowcount


def list_connectors(conn) -> list[dict]:
    """One row per live grant: who approved it, for which app, what it may do, when last seen."""
    rows = conn.execute(
        """
        SELECT t.family_id, t.username, t.scope, t.auth_time, c.client_name,
               (SELECT MAX(a.last_used_at) FROM oauth_tokens a WHERE a.family_id = t.family_id) AS last_used_at
        FROM oauth_tokens t LEFT JOIN oauth_clients c ON c.client_id = t.client_id
        WHERE t.revoked = 0 AND t.kind = 'refresh' AND t.used = 0 AND t.expires_at >= ?
        ORDER BY t.auth_time DESC
        """,
        (_stamp(_now()),),
    ).fetchall()
    return [dict(r) for r in rows]


def purge_expired(conn) -> None:
    """Drop long-dead rows. Called opportunistically; there is no scheduled job for this."""
    cutoff = _stamp(_now() - timedelta(days=7))
    conn.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (cutoff,))
    conn.execute("DELETE FROM oauth_tokens WHERE expires_at < ?", (cutoff,))
