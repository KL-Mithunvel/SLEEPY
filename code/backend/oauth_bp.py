"""
OAuth 2.1 authorization-server routes for the MCP connector.

  GET  /.well-known/oauth-protected-resource[/mcp]   RFC 9728 — "who authorizes /mcp?"
  GET  /.well-known/oauth-authorization-server       RFC 8414 — endpoints + capabilities
  POST /oauth/register                               RFC 7591 — dynamic client registration
  GET  /oauth/authorize                              consent + sign-in page
  POST /oauth/authorize                              sign in, approve/deny -> authorization code
  POST /oauth/token                                  code / refresh grant (PKCE S256 only)
  POST /oauth/revoke                                 RFC 7009

All of it is 404 unless config.MCP_ENABLED. Rules and storage live in
oauth_store.py; this file is transport, the consent page and abuse limits.

The sign-in step reuses the web login's lockout and login_events audit trail, so
guessing the password here is exactly as hard (and as visible) as on /api/auth/login.
"""

import logging
import threading
import time
from collections import defaultdict, deque
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, Response, jsonify, render_template_string, request

import auth_utils
import config
import oauth_store
from auth_bp import _MAX_USERNAME_LEN, _log_attempt

logger = logging.getLogger(__name__)

oauth_bp = Blueprint("oauth", __name__)

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def resource_url() -> str:
    return f"{config.PUBLIC_BASE_URL}/mcp"


def protected_resource_metadata_url() -> str:
    return f"{config.PUBLIC_BASE_URL}/.well-known/oauth-protected-resource"


def _db():
    from app import get_db
    return get_db()


@oauth_bp.before_request
def _gate():
    if not config.MCP_ENABLED:
        return jsonify({"error": "Not found"}), 404


# ---------------------------------------------------------------------------
# Per-IP limits — registration is open by design, so bound what it can cost
# ---------------------------------------------------------------------------

class _Limiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


register_limiter = _Limiter(10, 3600)
token_limiter = _Limiter(60, 60)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

def _json(payload: dict, status: int = 200) -> Response:
    resp = jsonify(payload)
    resp.status_code = status
    resp.headers.update(_NO_STORE)
    return resp


def _oauth_error(code: str, description: str = "", status: int = 400) -> Response:
    body = {"error": code}
    if description:
        body["error_description"] = description
    return _json(body, status)


def _page(html: str, status: int = 200) -> Response:
    resp = Response(html, status=status, mimetype="text/html")
    resp.headers.update(_NO_STORE)
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    return resp


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@oauth_bp.get("/.well-known/oauth-protected-resource")
@oauth_bp.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource():
    return jsonify({
        "resource": resource_url(),
        "authorization_servers": [config.PUBLIC_BASE_URL],
        "scopes_supported": list(oauth_store.SUPPORTED_SCOPES),
        "bearer_methods_supported": ["header"],
    })


@oauth_bp.get("/.well-known/oauth-authorization-server")
def authorization_server():
    base = config.PUBLIC_BASE_URL
    return jsonify({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "scopes_supported": list(oauth_store.SUPPORTED_SCOPES),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
    })


# ---------------------------------------------------------------------------
# Dynamic client registration
# ---------------------------------------------------------------------------

@oauth_bp.post("/oauth/register")
def register():
    if not register_limiter.allow(request.remote_addr or "?"):
        return _oauth_error("temporarily_unavailable", "Too many registrations", 429)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _oauth_error("invalid_client_metadata", "Body must be a JSON object")
    try:
        client = oauth_store.register_client(_db(), body.get("client_name"), body.get("redirect_uris"))
    except oauth_store.OAuthError as exc:
        return _oauth_error(exc.code, exc.description, 429 if exc.code == "temporarily_unavailable" else 400)
    return _json({
        "client_id": client["client_id"],
        "client_name": client["client_name"],
        "redirect_uris": client["redirect_uris"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": " ".join(oauth_store.SUPPORTED_SCOPES),
    }, 201)


# ---------------------------------------------------------------------------
# Authorization endpoint — consent page
# ---------------------------------------------------------------------------

_HIDDEN_FIELDS = ("client_id", "redirect_uri", "response_type", "state",
                  "code_challenge", "code_challenge_method", "scope", "resource")


class _PageError(Exception):
    """The request can't be trusted to name a redirect target: show an error, redirect nowhere."""


class _RedirectError(Exception):
    """The client and redirect URI are fine, so report back to the client the standard way."""

    def __init__(self, code: str, description: str, redirect_uri: str, state: str):
        super().__init__(code)
        self.code, self.description, self.redirect_uri, self.state = code, description, redirect_uri, state


def _validate_authorize(source) -> dict:
    client = oauth_store.get_client(_db(), source.get("client_id"))
    if client is None:
        raise _PageError("This application is not registered with SLEEPY.")
    redirect_uri = source.get("redirect_uri") or ""
    # Exact match against what the client registered AND what is allowed today.
    if redirect_uri not in client["redirect_uris"] or not oauth_store.redirect_uri_allowed(redirect_uri):
        raise _PageError("The redirect address does not match this application's registration.")

    state = (source.get("state") or "")[:512]

    def fail(code, description):
        return _RedirectError(code, description, redirect_uri, state)

    if source.get("response_type") != "code":
        raise fail("unsupported_response_type", "Only response_type=code is supported")
    if source.get("code_challenge_method") != "S256" or not oauth_store.valid_code_challenge(source.get("code_challenge")):
        raise fail("invalid_request", "PKCE with code_challenge_method=S256 is required")
    if source.get("resource") and source["resource"] != resource_url():
        raise fail("invalid_target", "Unknown resource")
    try:
        scopes = oauth_store.normalise_scopes(source.get("scope"))
    except oauth_store.OAuthError as exc:
        raise fail(exc.code, exc.description)

    return {
        "client": client, "redirect_uri": redirect_uri, "state": state,
        "code_challenge": source["code_challenge"], "scopes": scopes,
        "hidden": {k: (source.get(k) or "") for k in _HIDDEN_FIELDS},
    }


def _redirect_url(redirect_uri: str, params: dict) -> str:
    parts = urlsplit(redirect_uri)
    query = parse_qsl(parts.query, keep_blank_values=True) + [(k, v) for k, v in params.items() if v]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


# The browser is sent onward with a 200 + meta refresh rather than a 302: the
# site's CSP says `form-action 'self'`, and Chrome applies form-action to the
# redirect that follows a form POST, which would block the hop back to the app.
_REDIRECT_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="0;url={{ url }}"><title>Returning to the app…</title>
<style>body{font:16px system-ui,sans-serif;background:#111418;color:#e8eaed;display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}
a{color:#8ab4f8}</style></head><body><p>Returning to the app… <a href="{{ url }}">Tap here if nothing happens</a>.</p></body></html>"""


def _redirect_page(redirect_uri: str, params: dict) -> Response:
    return _page(render_template_string(_REDIRECT_PAGE, url=_redirect_url(redirect_uri, params)))


_CONSENT_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect to SLEEPY</title>
<style>
:root{color-scheme:dark}
body{font:16px/1.5 system-ui,sans-serif;background:#111418;color:#e8eaed;margin:0;padding:16px;display:grid;place-items:start center}
main{width:100%;max-width:26rem;background:#1a1f26;border:1px solid #2c333d;border-radius:12px;padding:20px;margin-top:6vh}
h1{font-size:1.25rem;margin:0 0 4px} p{margin:8px 0;color:#bdc1c6} .app{color:#e8eaed;font-weight:600}
ul{margin:8px 0 12px;padding-left:20px} li{margin:4px 0} .no{color:#9aa0a6;font-size:.9rem}
label{display:block;margin:12px 0 4px} input[type=text],input[type=password]{width:100%;box-sizing:border-box;font-size:16px;padding:12px;border-radius:8px;border:1px solid #3c4450;background:#0f1216;color:#e8eaed}
.check{display:flex;gap:10px;align-items:flex-start;margin:14px 0} .check input{margin-top:5px;width:20px;height:20px}
.row{display:flex;gap:10px;margin-top:16px} button{flex:1;font-size:16px;padding:13px;border-radius:8px;border:1px solid #3c4450;background:#262d37;color:#e8eaed;cursor:pointer}
button.go{background:#3b6fd4;border-color:#3b6fd4;color:#fff} .err{background:#3a1d1d;border:1px solid #6b2b2b;color:#f4b4b4;padding:10px;border-radius:8px;margin:12px 0}
</style></head><body><main>
<h1>Connect an app to SLEEPY</h1>
<p><span class="app">{{ client_name }}</span> is asking to connect. Approving sends you back to <b>{{ host }}</b>.</p>
<p>It will be able to:</p>
<ul>
 <li>See today's tasks and your project names</li>
 {% if can_write %}<li>Add tasks, mark tasks done and save quick notes</li>{% endif %}
</ul>
<p class="no">It will not be able to see your notes, news, briefings or files. You can revoke it at any time.</p>
{% if error %}<div class="err" role="alert">{{ error }}</div>{% endif %}
<form method="post" action="/oauth/authorize" autocomplete="on">
 {% for name, value in hidden.items() %}<input type="hidden" name="{{ name }}" value="{{ value }}">{% endfor %}
 <label for="u">Username</label><input id="u" name="username" type="text" autocomplete="username" autocapitalize="none" required maxlength="64" value="{{ username }}">
 <label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
 {% if can_write %}<div class="check"><input id="w" name="allow_write" type="checkbox" value="1" checked><label for="w" style="margin:0">Allow changes (add tasks, mark done, quick notes). Untick for read-only.</label></div>{% endif %}
 <div class="row"><button type="submit" name="action" value="deny" formnovalidate>Deny</button><button class="go" type="submit" name="action" value="approve">Approve</button></div>
</form></main></body></html>"""


def _consent(ctx: dict, error: str | None = None, status: int = 200, username: str = "") -> Response:
    return _page(render_template_string(
        _CONSENT_PAGE,
        client_name=ctx["client"]["client_name"],
        host=urlsplit(ctx["redirect_uri"]).netloc,
        can_write="tasks:write" in ctx["scopes"],
        hidden=ctx["hidden"], error=error, username=username,
    ), status)


def _error_page(message: str) -> Response:
    return _page(render_template_string(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<body style=\"font:16px system-ui;background:#111418;color:#e8eaed;padding:24px\">"
        "<h1 style='font-size:1.2rem'>Can't connect</h1><p>{{ m }}</p></body>", m=message), 400)


@oauth_bp.get("/oauth/authorize")
def authorize_get():
    try:
        ctx = _validate_authorize(request.args)
    except _PageError as exc:
        return _error_page(str(exc))
    except _RedirectError as exc:
        return _redirect_page(exc.redirect_uri, {"error": exc.code, "error_description": exc.description, "state": exc.state})
    return _consent(ctx)


@oauth_bp.post("/oauth/authorize")
def authorize_post():
    try:
        ctx = _validate_authorize(request.form)
    except _PageError as exc:
        return _error_page(str(exc))
    except _RedirectError as exc:
        return _redirect_page(exc.redirect_uri, {"error": exc.code, "error_description": exc.description, "state": exc.state})

    if request.form.get("action") == "deny":
        return _redirect_page(ctx["redirect_uri"], {"error": "access_denied", "state": ctx["state"]})

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if not username or not password or len(username) > _MAX_USERNAME_LEN:
        return _consent(ctx, "Enter your SLEEPY username and password.", 400, username[:_MAX_USERNAME_LEN])

    conn = _db()
    if auth_utils.is_locked_out(conn, username, request.remote_addr):
        logger.warning("oauth authorize: %r locked out", username)
        return _consent(ctx, "Too many failed attempts. Try again later.", 429, username)

    row = conn.execute("SELECT username, password_hash FROM users WHERE username = ?", (username,)).fetchone()
    if not auth_utils.verify_login(row, password):
        _log_attempt(conn, username, False)
        return _consent(ctx, "Invalid credentials.", 401, username)
    _log_attempt(conn, username, True)

    scopes = [s for s in ctx["scopes"] if s != "tasks:write" or request.form.get("allow_write") == "1"]
    if not scopes:
        return _redirect_page(ctx["redirect_uri"], {"error": "access_denied", "state": ctx["state"]})

    code = oauth_store.create_code(
        conn, client_id=ctx["client"]["client_id"], username=row["username"],
        redirect_uri=ctx["redirect_uri"], code_challenge=ctx["code_challenge"], scopes=scopes,
    )
    return _redirect_page(ctx["redirect_uri"], {"code": code, "state": ctx["state"]})


# ---------------------------------------------------------------------------
# Token + revocation endpoints
# ---------------------------------------------------------------------------

@oauth_bp.post("/oauth/token")
def token():
    if not token_limiter.allow(request.remote_addr or "?"):
        return _oauth_error("temporarily_unavailable", "Slow down", 429)
    form = request.form
    conn = _db()

    client_id = form.get("client_id") or ""
    if oauth_store.get_client(conn, client_id) is None:
        return _oauth_error("invalid_client", "Unknown client", 401)
    if form.get("resource") and form["resource"] != resource_url():
        return _oauth_error("invalid_target", "Unknown resource")

    grant = form.get("grant_type")
    try:
        if grant == "authorization_code":
            result = oauth_store.redeem_code(
                conn, code=form.get("code"), client_id=client_id,
                redirect_uri=form.get("redirect_uri") or "", code_verifier=form.get("code_verifier") or "",
            )
        elif grant == "refresh_token":
            result = oauth_store.refresh(
                conn, refresh_token=form.get("refresh_token"), client_id=client_id, scope=form.get("scope"),
            )
        else:
            return _oauth_error("unsupported_grant_type", "Use authorization_code or refresh_token")
    except oauth_store.OAuthError as exc:
        return _oauth_error(exc.code, exc.description)
    return _json(result)


@oauth_bp.post("/oauth/revoke")
def revoke():
    oauth_store.revoke_by_token(_db(), request.form.get("token"))
    return _json({})
