"""
OAuth 2.1 layer for the MCP connector — discovery, registration, consent/sign-in,
token grants, and the guarantee that connector tokens and web-session tokens
can never be swapped for one another.
"""

import base64
import hashlib
import html
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

import auth_utils
import config
import local_db
import mcp_tools
import oauth_bp
import oauth_store

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = "http://localhost:5000/mcp"


@pytest.fixture(autouse=True)
def oauth_env(monkeypatch, app, create_user):
    monkeypatch.setattr(config, "MCP_ENABLED", True)
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "k" * 40)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://localhost:5000")
    monkeypatch.setattr(config, "OAUTH_ALLOW_LOOPBACK_REDIRECTS", False)
    oauth_bp.register_limiter.reset()
    oauth_bp.token_limiter.reset()
    mcp_tools._write_times.clear()
    conn = local_db.get_db()
    try:
        for table in ("oauth_tokens", "oauth_codes", "oauth_clients", "login_events"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        local_db.return_db(conn)
    create_user("klm", "correct-horse-battery", "admin")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(client, name="Claude", redirect=REDIRECT):
    resp = client.post("/oauth/register", json={"client_name": name, "redirect_uris": [redirect]})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["client_id"]


def auth_params(client_id, challenge, **over):
    p = {"client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code", "state": "xyz",
         "code_challenge": challenge, "code_challenge_method": "S256"}
    p.update(over)
    return {k: v for k, v in p.items() if v is not None}


def redirect_target(resp):
    assert resp.status_code == 200, resp.data
    m = re.search(r'http-equiv="refresh" content="0;url=([^"]+)"', resp.get_data(as_text=True))
    assert m, resp.get_data(as_text=True)
    return urlsplit(html.unescape(m.group(1)))


def approve(client, client_id, challenge, *, password="correct-horse-battery", allow_write="1", **over):
    form = auth_params(client_id, challenge, **over)
    form.update({"username": "klm", "password": password, "action": "approve"})
    if allow_write:
        form["allow_write"] = allow_write
    return client.post("/oauth/authorize", data=form)


def get_code(client, client_id, challenge, **kw):
    target = redirect_target(approve(client, client_id, challenge, **kw))
    q = parse_qs(target.query)
    assert q["state"] == ["xyz"]
    return q["code"][0]


def exchange(client, client_id, code, verifier, **over):
    data = {"grant_type": "authorization_code", "code": code, "client_id": client_id,
            "redirect_uri": REDIRECT, "code_verifier": verifier}
    data.update(over)
    return client.post("/oauth/token", data=data)


def full_flow(client, **kw):
    client_id = register(client)
    verifier, challenge = pkce()
    code = get_code(client, client_id, challenge, **kw)
    resp = exchange(client, client_id, code, verifier)
    assert resp.status_code == 200, resp.get_json()
    return client_id, resp.get_json()


def mcp(client, token, method="tools/list", params=None, headers=None):
    headers = {**({"Authorization": f"Bearer {token}"} if token else {}), **(headers or {})}
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers)


def db_scalar(sql, *args):
    conn = local_db.get_db()
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        local_db.return_db(conn)


def time_travel(monkeypatch, **delta):
    monkeypatch.setattr(oauth_store, "_now", lambda: datetime.now() + timedelta(**delta))


# ---------------------------------------------------------------------------
# Off switch + discovery
# ---------------------------------------------------------------------------

def test_everything_404_when_disabled(client, monkeypatch):
    monkeypatch.setattr(config, "MCP_ENABLED", False)
    for method, path in (("get", "/.well-known/oauth-authorization-server"),
                         ("get", "/.well-known/oauth-protected-resource"),
                         ("post", "/oauth/register"), ("post", "/oauth/token"),
                         ("get", "/oauth/authorize"), ("post", "/mcp")):
        assert getattr(client, method)(path).status_code == 404, path


def test_authorization_server_metadata(client):
    m = client.get("/.well-known/oauth-authorization-server").get_json()
    assert m["issuer"] == "http://localhost:5000"
    assert m["code_challenge_methods_supported"] == ["S256"]
    assert m["token_endpoint_auth_methods_supported"] == ["none"]
    assert m["grant_types_supported"] == ["authorization_code", "refresh_token"]
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint", "revocation_endpoint"):
        assert m[key].startswith("http://localhost:5000/oauth/")


def test_protected_resource_metadata_both_paths(client):
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        m = client.get(path).get_json()
        assert m["resource"] == RESOURCE
        assert m["authorization_servers"] == ["http://localhost:5000"]
        assert set(m["scopes_supported"]) == {"tasks:read", "tasks:write"}


def test_metadata_ignores_spoofed_host_header(client):
    m = client.get("/.well-known/oauth-authorization-server", headers={"Host": "evil.example"}).get_json()
    assert "evil.example" not in str(m)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_returns_public_client(client):
    resp = client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [REDIRECT]})
    body = resp.get_json()
    assert resp.status_code == 201
    assert body["token_endpoint_auth_method"] == "none" and "client_secret" not in body
    assert body["redirect_uris"] == [REDIRECT]


@pytest.mark.parametrize("uri", [
    "https://evil.example/cb",
    "https://claude.ai/api/mcp/auth_callback/extra",
    "https://claude.ai.evil.example/api/mcp/auth_callback",
    "http://localhost:8080/cb",       # loopback is off
    "javascript:alert(1)",
    "",
])
def test_register_rejects_redirects_outside_the_allowlist(client, uri):
    resp = client.post("/oauth/register", json={"client_name": "x", "redirect_uris": [uri]})
    assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_redirect_uri"


def test_register_accepts_loopback_only_when_enabled(client, monkeypatch):
    monkeypatch.setattr(config, "OAUTH_ALLOW_LOOPBACK_REDIRECTS", True)
    ok = client.post("/oauth/register", json={"client_name": "i", "redirect_uris": ["http://localhost:6274/oauth/callback"]})
    assert ok.status_code == 201
    bad = client.post("/oauth/register", json={"client_name": "i", "redirect_uris": ["http://evil.example:6274/cb"]})
    assert bad.status_code == 400


@pytest.mark.parametrize("body", [None, [], {"client_name": "x"}, {"client_name": "x", "redirect_uris": []},
                                  {"client_name": "x", "redirect_uris": REDIRECT},
                                  {"client_name": "x", "redirect_uris": [REDIRECT] * 6}])
def test_register_rejects_malformed_bodies(client, body):
    assert client.post("/oauth/register", json=body).status_code == 400


def test_register_is_rate_limited(client):
    for _ in range(10):
        assert client.post("/oauth/register", json={"client_name": "x", "redirect_uris": [REDIRECT]}).status_code == 201
    assert client.post("/oauth/register", json={"client_name": "x", "redirect_uris": [REDIRECT]}).status_code == 429


def test_register_enforces_client_cap(client, monkeypatch):
    monkeypatch.setattr(config, "OAUTH_MAX_CLIENTS", 2)
    register(client), register(client)
    resp = client.post("/oauth/register", json={"client_name": "x", "redirect_uris": [REDIRECT]})
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# Authorization endpoint
# ---------------------------------------------------------------------------

def test_consent_page_shows_app_and_destination(client):
    cid = register(client, "My Claude")
    _, challenge = pkce()
    resp = client.get("/oauth/authorize", query_string=auth_params(cid, challenge))
    page = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "My Claude" in page and "claude.ai" in page
    assert "will not be able to see your notes, news, briefings or files" in page
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "no-store" in resp.headers["Cache-Control"]
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]


def test_client_name_cannot_inject_markup(client):
    cid = register(client, '<script>alert(1)</script><img src=x onerror=alert(2)>')
    _, challenge = pkce()
    page = client.get("/oauth/authorize", query_string=auth_params(cid, challenge)).get_data(as_text=True)
    assert "<script>alert" not in page and "<img src=x" not in page
    assert "&lt;script&gt;" in page


def test_unknown_client_is_an_error_page_not_a_redirect(client):
    _, challenge = pkce()
    resp = client.get("/oauth/authorize", query_string=auth_params("nope", challenge))
    assert resp.status_code == 400 and "location" not in {k.lower() for k, _ in resp.headers}
    assert "refresh" not in resp.get_data(as_text=True)


def test_unregistered_redirect_is_an_error_page_not_a_redirect(client):
    cid = register(client)
    _, challenge = pkce()
    resp = client.get("/oauth/authorize", query_string=auth_params(cid, challenge, redirect_uri="https://evil.example/cb"))
    assert resp.status_code == 400 and "evil.example" not in resp.get_data(as_text=True).replace("redirect address", "")
    assert "http-equiv" not in resp.get_data(as_text=True)


@pytest.mark.parametrize("over,error", [
    ({"code_challenge": None}, "invalid_request"),
    ({"code_challenge_method": "plain"}, "invalid_request"),
    ({"code_challenge_method": None}, "invalid_request"),
    ({"code_challenge": "short"}, "invalid_request"),
    ({"response_type": "token"}, "unsupported_response_type"),
    ({"scope": "tasks:read admin"}, "invalid_scope"),
    ({"resource": "https://other.example/mcp"}, "invalid_target"),
])
def test_bad_authorize_requests_redirect_back_with_error(client, over, error):
    cid = register(client)
    _, challenge = pkce()
    resp = client.get("/oauth/authorize", query_string=auth_params(cid, challenge, **over))
    target = redirect_target(resp)
    q = parse_qs(target.query)
    assert f"{target.scheme}://{target.netloc}{target.path}" == REDIRECT
    assert q["error"] == [error] and q["state"] == ["xyz"] and "code" not in q


def test_correct_resource_is_accepted(client):
    cid = register(client)
    _, challenge = pkce()
    assert client.get("/oauth/authorize", query_string=auth_params(cid, challenge, resource=RESOURCE)).status_code == 200


def test_deny_returns_access_denied_and_no_code(client):
    cid = register(client)
    _, challenge = pkce()
    form = auth_params(cid, challenge)
    form["action"] = "deny"
    q = parse_qs(redirect_target(client.post("/oauth/authorize", data=form)).query)
    assert q["error"] == ["access_denied"] and "code" not in q
    assert db_scalar("SELECT COUNT(*) FROM oauth_codes") == 0


def test_wrong_password_gets_no_code_and_is_logged(client):
    cid = register(client)
    _, challenge = pkce()
    resp = approve(client, cid, challenge, password="wrong")
    assert resp.status_code == 401 and "Invalid credentials" in resp.get_data(as_text=True)
    assert db_scalar("SELECT COUNT(*) FROM oauth_codes") == 0
    assert db_scalar("SELECT COUNT(*) FROM login_events WHERE username='klm' AND success=0") == 1


def test_unknown_user_looks_the_same_as_wrong_password(client):
    cid = register(client)
    _, challenge = pkce()
    form = auth_params(cid, challenge)
    form.update({"username": "ghost", "password": "whatever-123", "action": "approve"})
    resp = client.post("/oauth/authorize", data=form)
    assert resp.status_code == 401 and "Invalid credentials" in resp.get_data(as_text=True)


def test_password_guessing_here_hits_the_same_lockout_as_web_login(client):
    cid = register(client)
    _, challenge = pkce()
    for _ in range(5):
        assert approve(client, cid, challenge, password="nope").status_code == 401
    locked = approve(client, cid, challenge)  # even the CORRECT password is refused now
    assert locked.status_code == 429
    assert db_scalar("SELECT COUNT(*) FROM oauth_codes") == 0
    # ... and the web login is locked by the very same counter
    web = client.post("/api/auth/login", json={"username": "klm", "password": "correct-horse-battery"})
    assert web.status_code == 429


def test_successful_approval_returns_code_and_state(client):
    cid = register(client)
    _, challenge = pkce()
    target = redirect_target(approve(client, cid, challenge))
    q = parse_qs(target.query)
    assert q["state"] == ["xyz"] and q["code"][0].startswith("slc_")


def test_code_is_stored_only_as_a_hash(client):
    cid = register(client)
    _, challenge = pkce()
    code = get_code(client, cid, challenge)
    assert db_scalar("SELECT COUNT(*) FROM oauth_codes WHERE code_hash = ?", code) == 0
    assert db_scalar("SELECT COUNT(*) FROM oauth_codes WHERE code_hash = ?", oauth_store._hash(code)) == 1


def test_unticking_allow_changes_grants_read_only(client):
    cid = register(client)
    _, challenge = pkce()
    code = get_code(client, cid, challenge, allow_write=None)
    assert db_scalar("SELECT scope FROM oauth_codes") == "tasks:read"
    assert code


def test_tampered_hidden_fields_are_revalidated_on_post(client):
    cid = register(client)
    _, challenge = pkce()
    resp = approve(client, cid, challenge, redirect_uri="https://evil.example/cb")
    assert resp.status_code == 400
    assert db_scalar("SELECT COUNT(*) FROM oauth_codes") == 0


# ---------------------------------------------------------------------------
# Token endpoint — authorization_code
# ---------------------------------------------------------------------------

def test_full_flow_returns_bearer_pair(client):
    _, tokens = full_flow(client)
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
    assert tokens["access_token"].startswith("sla_") and tokens["refresh_token"].startswith("slr_")
    assert set(tokens["scope"].split()) == {"tasks:read", "tasks:write"}


def test_token_responses_are_not_cacheable(client):
    cid = register(client)
    verifier, challenge = pkce()
    resp = exchange(client, cid, get_code(client, cid, challenge), verifier)
    assert "no-store" in resp.headers["Cache-Control"]


def test_tokens_are_stored_only_as_hashes(client):
    _, tokens = full_flow(client)
    for raw in (tokens["access_token"], tokens["refresh_token"]):
        assert db_scalar("SELECT COUNT(*) FROM oauth_tokens WHERE token_hash = ?", raw) == 0
        assert db_scalar("SELECT COUNT(*) FROM oauth_tokens WHERE token_hash = ?", oauth_store._hash(raw)) == 1


def test_wrong_pkce_verifier_is_refused(client):
    cid = register(client)
    _, challenge = pkce()
    code = get_code(client, cid, challenge)
    other_verifier, _ = pkce()
    resp = exchange(client, cid, code, other_verifier)
    assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_grant"
    assert db_scalar("SELECT COUNT(*) FROM oauth_tokens") == 0


def test_missing_or_malformed_verifier_is_refused(client):
    cid = register(client)
    _, challenge = pkce()
    code = get_code(client, cid, challenge)
    for bad in ("", "short", "a" * 200, "has spaces " * 6):
        assert exchange(client, cid, code, bad).status_code == 400


def test_code_cannot_be_redeemed_twice_and_reuse_revokes_the_grant(client):
    cid = register(client)
    verifier, challenge = pkce()
    code = get_code(client, cid, challenge)
    first = exchange(client, cid, code, verifier)
    access = first.get_json()["access_token"]
    assert mcp(client, access).status_code == 200
    replay = exchange(client, cid, code, verifier)
    assert replay.status_code == 400 and replay.get_json()["error"] == "invalid_grant"
    assert mcp(client, access).status_code == 401    # what the code produced is now dead


def test_code_is_bound_to_its_client_and_redirect(client):
    cid, other = register(client), register(client, "Other")
    verifier, challenge = pkce()
    code = get_code(client, cid, challenge)
    assert exchange(client, other, code, verifier).status_code == 400
    assert exchange(client, cid, code, verifier, redirect_uri="https://claude.com/api/mcp/auth_callback").status_code == 400
    assert exchange(client, cid, code, verifier).status_code == 200   # the real one still works


def test_expired_code_is_refused(client, monkeypatch):
    cid = register(client)
    verifier, challenge = pkce()
    code = get_code(client, cid, challenge)
    time_travel(monkeypatch, seconds=config.OAUTH_CODE_TTL_SECONDS + 5)
    assert exchange(client, cid, code, verifier).get_json()["error"] == "invalid_grant"


def test_unknown_code_unknown_client_and_bad_grant_type(client):
    cid = register(client)
    verifier, _ = pkce()
    assert exchange(client, cid, "slc_made_up", verifier).get_json()["error"] == "invalid_grant"
    assert exchange(client, "nope", "x", verifier).status_code == 401
    resp = client.post("/oauth/token", data={"grant_type": "password", "client_id": cid})
    assert resp.get_json()["error"] == "unsupported_grant_type"
    resp = client.post("/oauth/token", data={"grant_type": "client_credentials", "client_id": cid})
    assert resp.get_json()["error"] == "unsupported_grant_type"


def test_token_resource_param_must_match(client):
    cid = register(client)
    verifier, challenge = pkce()
    code = get_code(client, cid, challenge)
    assert exchange(client, cid, code, verifier, resource="https://other.example/mcp").get_json()["error"] == "invalid_target"


def test_token_endpoint_is_rate_limited(client):
    cid = register(client)
    codes = [exchange(client, cid, "slc_x", "v" * 50).status_code for _ in range(61)]
    assert codes[-1] == 429


def test_deleted_user_cannot_complete_the_exchange(client, create_user):
    cid = register(client)
    verifier, challenge = pkce()
    code = get_code(client, cid, challenge)
    conn = local_db.get_db()
    conn.execute("DELETE FROM users WHERE username='klm'")
    conn.commit()
    local_db.return_db(conn)
    assert exchange(client, cid, code, verifier).status_code == 400


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------

def refresh(client, cid, token, **extra):
    return client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": cid,
                                             "refresh_token": token, **extra})


def test_refresh_rotates_and_new_access_token_works(client):
    cid, t1 = full_flow(client)
    resp = refresh(client, cid, t1["refresh_token"])
    t2 = resp.get_json()
    assert resp.status_code == 200 and t2["refresh_token"] != t1["refresh_token"]
    assert mcp(client, t2["access_token"]).status_code == 200


def test_reusing_a_spent_refresh_token_revokes_the_whole_grant(client):
    cid, t1 = full_flow(client)
    t2 = refresh(client, cid, t1["refresh_token"]).get_json()
    assert refresh(client, cid, t1["refresh_token"]).get_json()["error"] == "invalid_grant"
    # the thief's replay also killed the legitimate party's newer tokens
    assert mcp(client, t2["access_token"]).status_code == 401
    assert refresh(client, cid, t2["refresh_token"]).get_json()["error"] == "invalid_grant"


def test_refresh_is_bound_to_its_client(client):
    cid, t1 = full_flow(client)
    other = register(client, "Other")
    assert refresh(client, other, t1["refresh_token"]).get_json()["error"] == "invalid_grant"


def test_refresh_can_narrow_but_never_widen_scope(client):
    cid, t1 = full_flow(client, allow_write=None)       # granted read only
    assert refresh(client, cid, t1["refresh_token"], scope="tasks:read tasks:write").get_json()["error"] == "invalid_scope"
    narrowed = refresh(client, cid, t1["refresh_token"], scope="tasks:read").get_json()
    assert narrowed["scope"] == "tasks:read"


def test_access_token_cannot_be_used_as_a_refresh_token(client):
    cid, t1 = full_flow(client)
    assert refresh(client, cid, t1["access_token"]).get_json()["error"] == "invalid_grant"


def test_expired_refresh_token_is_refused(client, monkeypatch):
    cid, t1 = full_flow(client)
    time_travel(monkeypatch, days=config.OAUTH_REFRESH_TTL_DAYS + 1)
    assert refresh(client, cid, t1["refresh_token"]).get_json()["error"] == "invalid_grant"


def test_grant_dies_after_max_age_however_often_refreshed(client, monkeypatch):
    cid, t = full_flow(client)
    monkeypatch.setattr(config, "OAUTH_GRANT_MAX_DAYS", 3)
    for day in (1, 2):                                   # keep refreshing inside the window
        time_travel(monkeypatch, days=day)
        t = refresh(client, cid, t["refresh_token"]).get_json()
        assert "access_token" in t
    time_travel(monkeypatch, days=4)
    assert refresh(client, cid, t["refresh_token"]).get_json()["error"] == "invalid_grant"
    assert mcp(client, t["access_token"]).status_code == 401


# ---------------------------------------------------------------------------
# /mcp with OAuth
# ---------------------------------------------------------------------------

def test_no_token_gets_401_with_discovery_pointer(client):
    resp = mcp(client, None)
    assert resp.status_code == 401
    challenge = resp.headers["WWW-Authenticate"]
    assert 'resource_metadata="http://localhost:5000/.well-known/oauth-protected-resource"' in challenge
    assert "invalid_token" not in challenge


def test_bad_token_gets_invalid_token_challenge(client):
    resp = mcp(client, "sla_" + "x" * 40)
    assert resp.status_code == 401 and 'error="invalid_token"' in resp.headers["WWW-Authenticate"]


def test_valid_token_lists_all_five_tools(client):
    _, t = full_flow(client)
    names = {x["name"] for x in mcp(client, t["access_token"]).get_json()["result"]["tools"]}
    assert names == {"get_today", "list_projects", "add_task", "capture", "toggle_task"}


def test_read_only_grant_cannot_see_or_call_write_tools(client):
    _, t = full_flow(client, allow_write=None)
    names = {x["name"] for x in mcp(client, t["access_token"]).get_json()["result"]["tools"]}
    assert names == {"get_today", "list_projects"}
    call = mcp(client, t["access_token"], "tools/call",
               {"name": "capture", "arguments": {"text": "sneaky"}}).get_json()
    assert call["error"]["code"] == -32602


def test_web_session_jwt_is_not_accepted_on_mcp(client):
    session = auth_utils.issue_token("klm", "admin", 0)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {session}"}).status_code == 200
    assert mcp(client, session).status_code == 401


def test_connector_token_is_not_accepted_on_the_app_api(client):
    _, t = full_flow(client)
    for path in ("/api/today", "/api/projects", "/api/auth/me", "/api/admin/login-events"):
        resp = client.get(path, headers={"Authorization": f"Bearer {t['access_token']}"})
        assert resp.status_code == 401, path
    assert client.post("/api/today/capture", json={"text": "x"},
                       headers={"Authorization": f"Bearer {t['refresh_token']}"}).status_code == 401


def test_oauth_paths_do_not_open_the_api_without_a_token(client):
    for path in ("/api/today", "/api/projects", "/api/ai/chat", "/oauth/../api/today"):
        assert client.get(path).status_code in (401, 404), path


def test_expired_access_token_is_refused(client, monkeypatch):
    _, t = full_flow(client)
    time_travel(monkeypatch, minutes=config.OAUTH_ACCESS_TTL_MINUTES + 1)
    assert mcp(client, t["access_token"]).status_code == 401


def test_deleting_the_user_kills_their_connector(client):
    _, t = full_flow(client)
    conn = local_db.get_db()
    conn.execute("DELETE FROM users WHERE username='klm'")
    conn.commit()
    local_db.return_db(conn)
    assert mcp(client, t["access_token"]).status_code == 401


def test_origin_check_still_applies_with_a_valid_token(client):
    _, t = full_flow(client)
    assert mcp(client, t["access_token"], headers={"Origin": "https://evil.example"}).status_code == 403


def test_dev_bypass_without_header_still_works_locally(client, monkeypatch):
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", True)
    assert mcp(client, None).status_code == 200
    assert mcp(client, "sla_garbage").status_code == 401          # a presented token is still verified


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------

def test_revoke_endpoint_kills_the_whole_grant(client):
    cid, t = full_flow(client)
    assert client.post("/oauth/revoke", data={"token": t["refresh_token"]}).status_code == 200
    assert mcp(client, t["access_token"]).status_code == 401
    assert refresh(client, cid, t["refresh_token"]).get_json()["error"] == "invalid_grant"


def test_revoke_unknown_token_is_silent(client):
    assert client.post("/oauth/revoke", data={"token": "nonsense"}).status_code == 200
    assert client.post("/oauth/revoke").status_code == 200


def test_revoke_all_for_user_and_listing(client):
    _, t = full_flow(client)
    conn = local_db.get_db()
    try:
        grants = oauth_store.list_connectors(conn)
        assert len(grants) == 1 and grants[0]["username"] == "klm" and grants[0]["client_name"] == "Claude"
        assert oauth_store.revoke_all_for_user(conn, "klm") >= 2
        assert oauth_store.list_connectors(conn) == []
    finally:
        local_db.return_db(conn)
    assert mcp(client, t["access_token"]).status_code == 401


def test_last_used_is_recorded(client):
    _, t = full_flow(client)
    mcp(client, t["access_token"])
    conn = local_db.get_db()
    try:
        assert oauth_store.list_connectors(conn)[0]["last_used_at"]
    finally:
        local_db.return_db(conn)


def test_tools_still_enforce_their_own_checks_over_oauth(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path))
    (tmp_path / "VIT").mkdir()
    (tmp_path / "VIT" / "magnet.md").write_text("---\nstatus: active\n---\n# M\n", encoding="utf-8")
    _, t = full_flow(client)
    ok = mcp(client, t["access_token"], "tools/call",
             {"name": "add_task", "arguments": {"task": "via oauth", "project": "VIT/magnet.md"}}).get_json()
    assert ok["result"]["isError"] is False
    bad = mcp(client, t["access_token"], "tools/call",
              {"name": "add_task", "arguments": {"task": "x", "project": "../../etc/passwd"}}).get_json()
    assert bad["result"]["isError"] is True
