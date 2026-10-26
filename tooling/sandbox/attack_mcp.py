"""
Attacks on the MCP connector (OAuth 2.1 server + /mcp), against the running
sandbox. Start run_sandbox.py first, then:

    uv run python tooling/sandbox/attack_mcp.py

Two attacker positions: an outsider who has read the public source, and a
connector that WAS legitimately approved but is now being steered by hostile
text (prompt injection) or whose token leaked.

Each check prints BLOCKED (defence held), VULN (attack worked) or INFO.
Exit code = number of VULNs. Re-runnable: attacker traffic uses throwaway
X-Forwarded-For addresses so per-IP limits and lockouts don't starve later runs;
the write-flood check (last-but-one) uses up the connector's hourly write budget,
which resets when the sandbox server restarts.
"""

import base64
import hashlib
import html
import json
import random
import re
import secrets
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

import sandbox_env as sb

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
RESULTS: list[tuple[str, str, str]] = []


def report(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))
    colour = {"BLOCKED": "\033[32m", "VULN": "\033[31m", "INFO": "\033[33m"}[status]
    print(f"{colour}{status:8}\033[0m {name}" + (f"  — {detail}" if detail else ""))


def check(ok: bool, name: str, detail: str = "") -> bool:
    report("BLOCKED" if ok else "VULN", name, detail)
    return ok


def fresh_ip() -> str:
    return f"203.0.113.{random.randint(1, 254)}"


def http(method, path, *, headers=None, json_body=None, form=None, raw=None, ip=None):
    h = dict(headers or {})
    h["X-Forwarded-For"] = ip or fresh_ip()
    data = raw
    if json_body is not None:
        data = json.dumps(json_body).encode()
        h["Content-Type"] = "application/json"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(sb.BASE_URL + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(name="Claude", redirect=REDIRECT, ip=None):
    return http("POST", "/oauth/register", json_body={"client_name": name, "redirect_uris": [redirect]}, ip=ip)


def new_client(name="Claude") -> str:
    s, b, _ = register(name)
    assert s == 201, f"register failed: {s} {b}"
    return json.loads(b)["client_id"]


def auth_form(cid, challenge, **over):
    f = {"client_id": cid, "redirect_uri": REDIRECT, "response_type": "code", "state": "st",
         "code_challenge": challenge, "code_challenge_method": "S256"}
    f.update(over)
    return {k: v for k, v in f.items() if v is not None}


def refresh_target(body: str) -> str | None:
    m = re.search(r'http-equiv="refresh" content="0;url=([^"]+)"', body)
    return html.unescape(m.group(1)) if m else None


def approve(cid, challenge, *, user="owner", password=None, write=True, ip=None, **over):
    form = auth_form(cid, challenge, **over)
    form.update({"username": user, "password": password or sb.USERS[user][0], "action": "approve"})
    if write:
        form["allow_write"] = "1"
    return http("POST", "/oauth/authorize", form=form, ip=ip)


def code_from(body: str) -> str | None:
    target = refresh_target(body)
    if not target:
        return None
    return urllib.parse.parse_qs(urllib.parse.urlsplit(target).query).get("code", [None])[0]


def exchange(cid, code, verifier, **over):
    form = {"grant_type": "authorization_code", "code": code, "client_id": cid,
            "redirect_uri": REDIRECT, "code_verifier": verifier}
    form.update(over)
    return http("POST", "/oauth/token", form=form)


_PRIMARY: list[str] = []


def grant(write=True) -> tuple[str, dict]:
    if not _PRIMARY:                      # one registered client reused for every grant
        _PRIMARY.append(new_client())
    cid = _PRIMARY[0]
    verifier, challenge = pkce()
    s, b, _ = approve(cid, challenge, write=write)
    code = code_from(b)
    assert code, f"no code: {s} {b[:200]}"
    s, b, _ = exchange(cid, code, verifier)
    assert s == 200, f"exchange failed: {s} {b}"
    return cid, json.loads(b)


def rpc(token, method, params=None, *, headers=None, ip=None, raw=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    if raw is not None:
        return http("POST", "/mcp", headers={**h, "Content-Type": "application/json"}, raw=raw, ip=ip)
    return http("POST", "/mcp", headers=h, json_body=body, ip=ip)


def call(token, name, **args):
    s, b, _ = rpc(token, "tools/call", {"name": name, "arguments": args})
    try:
        return s, json.loads(b)
    except ValueError:
        return s, {}


def leaked(text: str) -> str | None:
    for c in (sb.CANARY_OUT, sb.CANARY_DB, sb.CANARY_SRC, sb.CANARY_IN):
        if c in text:
            return c
    return None


# ---------------------------------------------------------------------------
# 1. Registration + redirect handling (the only thing that decides who gets a code)
# ---------------------------------------------------------------------------

def t_registration_redirects():
    for uri in ("https://evil.example/cb", "https://claude.ai.evil.example/api/mcp/auth_callback",
                "https://claude.ai/api/mcp/auth_callback/x", "https://claude.ai/api/mcp/auth_callback?x=1",
                "https://claude.ai/api/mcp/auth_callback#frag", "http://127.0.0.1:6274/cb",
                "http://localhost/cb", "javascript:alert(1)", "data:text/html,x", "//evil.example/cb",
                "https://claude.ai@evil.example/api/mcp/auth_callback"):
        s, _, _ = register(redirect=uri)
        check(s == 400, f"Register redirect_uri {uri!r}", f"status {s}")


def t_authorize_redirect_tampering():
    cid = new_client()
    _, challenge = pkce()
    for uri in ("https://evil.example/cb", "https://claude.com/api/mcp/auth_callback"):
        form = auth_form(cid, challenge, redirect_uri=uri)
        q = urllib.parse.urlencode(form)
        s, b, h = http("GET", f"/oauth/authorize?{q}")
        target = refresh_target(b)
        check(s == 400 and target is None and "location" not in {k.lower() for k in h},
              f"Authorize with unregistered redirect {uri}", f"status {s}")
    # same through the POST (hidden-field tampering) with the correct password
    s, b, _ = approve(cid, challenge, redirect_uri="https://evil.example/cb")
    check(s == 400 and code_from(b) is None, "Authorize POST with swapped redirect + valid password", f"status {s}")
    s, b, _ = http("GET", "/oauth/authorize?" + urllib.parse.urlencode(auth_form("nonexistent", challenge)))
    check(s == 400 and refresh_target(b) is None, "Authorize with unknown client_id", f"status {s}")


def t_pkce_required():
    cid = new_client()
    verifier, challenge = pkce()
    for name, over in (("no challenge", {"code_challenge": None}), ("plain method", {"code_challenge_method": "plain"}),
                       ("no method", {"code_challenge_method": None}), ("short challenge", {"code_challenge": "abc"}),
                       ("implicit flow", {"response_type": "token"})):
        s, b, _ = approve(cid, challenge, **over)
        check(code_from(b) is None, f"Authorize without PKCE/with weak PKCE ({name})", f"status {s}")


def t_code_theft():
    cid = new_client()
    verifier, challenge = pkce()
    s, b, _ = approve(cid, challenge)
    code = code_from(b)
    # attacker intercepts the redirect (code + state) but not the verifier
    for bad in ("", "x" * 43, secrets.token_urlsafe(48)):
        s, _, _ = exchange(cid, code, bad)
        check(s == 400, f"Redeem stolen code with wrong/blank verifier ({bad[:8]!r})", f"status {s}")
    other = new_client("Other")
    s, _, _ = exchange(other, code, verifier)
    check(s == 400, "Redeem code as a different client", f"status {s}")
    s, _, _ = exchange(cid, code, verifier, redirect_uri="https://claude.com/api/mcp/auth_callback")
    check(s == 400, "Redeem code with a different redirect_uri", f"status {s}")


def t_code_replay():
    cid = new_client()
    verifier, challenge = pkce()
    code = code_from(approve(cid, challenge)[1])
    s, b, _ = exchange(cid, code, verifier)
    access = json.loads(b)["access_token"]
    s1, _, _ = rpc(access, "tools/list")
    s2, _, _ = exchange(cid, code, verifier)
    s3, _, _ = rpc(access, "tools/list")
    check(s1 == 200 and s2 == 400 and s3 == 401, "Replayed auth code is refused and kills the tokens it made",
          f"list {s1} -> replay {s2} -> list {s3}")


def t_refresh_attacks():
    cid, t = grant()
    s, b, _ = http("POST", "/oauth/token", form={"grant_type": "refresh_token", "client_id": cid, "refresh_token": t["refresh_token"]})
    new = json.loads(b)
    s2, _, _ = http("POST", "/oauth/token", form={"grant_type": "refresh_token", "client_id": cid, "refresh_token": t["refresh_token"]})
    s3, _, _ = rpc(new["access_token"], "tools/list")
    check(s == 200 and s2 == 400 and s3 == 401, "Replayed refresh token is refused and revokes the grant",
          f"rotate {s} -> replay {s2} -> newer access {s3}")

    cid, ro = grant(write=False)
    s, b, _ = http("POST", "/oauth/token", form={"grant_type": "refresh_token", "client_id": cid,
                                                  "refresh_token": ro["refresh_token"], "scope": "tasks:read tasks:write"})
    check(s == 400 and "invalid_scope" in b, "Widen read-only grant to write via refresh", f"status {s}")

    other = new_client("Other")
    cid, t = grant()
    s, _, _ = http("POST", "/oauth/token", form={"grant_type": "refresh_token", "client_id": other, "refresh_token": t["refresh_token"]})
    check(s == 400, "Use another client's refresh token", f"status {s}")
    s, _, _ = http("POST", "/oauth/token", form={"grant_type": "refresh_token", "client_id": cid, "refresh_token": t["access_token"]})
    check(s == 400, "Use an access token as a refresh token", f"status {s}")
    for gt in ("password", "client_credentials", "implicit", "urn:ietf:params:oauth:grant-type:device_code"):
        s, _, _ = http("POST", "/oauth/token", form={"grant_type": gt, "client_id": cid, "username": "owner", "password": sb.USERS["owner"][0]})
        check(s == 400, f"Token endpoint grant_type={gt}", f"status {s}")


# ---------------------------------------------------------------------------
# 2. Credential confusion — the two token families must not cross
# ---------------------------------------------------------------------------

def t_token_confusion():
    cid, t = grant()
    s, b, _ = http("POST", "/api/auth/login", json_body={"username": "owner", "password": sb.USERS["owner"][0]})
    session = json.loads(b)["token"]
    s, _, h = rpc(session, "tools/list")
    check(s == 401, "Web-session JWT presented to /mcp", f"status {s}")
    check("resource_metadata" in h.get("WWW-Authenticate", ""), "401 from /mcp carries the discovery pointer")
    bad = []
    for token in (t["access_token"], t["refresh_token"]):
        for method, path in (("GET", "/api/today"), ("GET", "/api/projects"), ("GET", "/api/auth/me"),
                             ("GET", "/api/admin/login-events"), ("GET", "/api/admin/system"),
                             ("POST", "/api/today/capture"), ("POST", "/api/ai/chat")):
            s, _, _ = http(method, path, headers={"Authorization": f"Bearer {token}"}, json_body={} if method == "POST" else None)
            if s != 401:
                bad.append(f"{method} {path} -> {s}")
    check(not bad, "Connector access/refresh token presented to /api/*", "; ".join(bad))
    s, _, _ = rpc(t["refresh_token"], "tools/list")
    check(s == 401, "Refresh token presented to /mcp", f"status {s}")
    for junk in ("", "Bearer", "Bearer ", "bearer " + t["access_token"], "Basic " + t["access_token"],
                 "Bearer sla_" + "A" * 43, "Bearer " + "sla_' OR 1=1 --", "Bearer sla_" + "A" * 5000):
        s, _, _ = http("POST", "/mcp", headers={"Authorization": junk}, json_body={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        check(s == 401, f"/mcp with Authorization {junk[:28]!r}", f"status {s}")


def t_no_token_anywhere():
    s, _, _ = rpc(None, "tools/list")
    check(s == 401, "/mcp with no credentials", f"status {s}")
    s, _, _ = http("GET", "/mcp")
    check(s in (401, 405), "GET /mcp with no credentials", f"status {s}")


def t_origin():
    _, t = grant()
    s, _, _ = rpc(t["access_token"], "tools/list", headers={"Origin": "https://evil.example"})
    check(s == 403, "/mcp from a browser page on another origin (valid token)", f"status {s}")


# ---------------------------------------------------------------------------
# 3. A legitimately-approved connector steered by hostile text
# ---------------------------------------------------------------------------

def t_scope_enforcement():
    _, ro = grant(write=False)
    s, body = call(ro["access_token"], "capture", text="read-only token writing")
    check("error" in body and s == 200, "Read-only grant calling capture", f"-> {str(body)[:80]}")
    for tool, args in (("add_task", {"task": "x", "project": "VIT/secret-project.md"}),
                       ("toggle_task", {"rel_path": "VIT/secret-project.md", "exact_text": "do the thing"})):
        s, body = call(ro["access_token"], tool, **args)
        check("error" in body, f"Read-only grant calling {tool}", f"-> {str(body)[:80]}")
    s, b, _ = rpc(ro["access_token"], "tools/list")
    names = {t["name"] for t in json.loads(b)["result"]["tools"]}
    check(names == {"get_today", "list_projects"}, "Read-only grant's tool list hides write tools", str(sorted(names)))


def t_tool_surface():
    _, t = grant()
    s, b, _ = rpc(t["access_token"], "tools/list")
    names = {x["name"] for x in json.loads(b)["result"]["tools"]}
    check(names == {"get_today", "list_projects", "add_task", "capture", "toggle_task"}, "Exactly five tools exposed", str(sorted(names)))
    for tool in ("read_file", "write_file", "grep", "search", "list_files", "move_file", "delete_file",
                 "get_briefing", "get_news", "send_email", "run_command", "load_skill"):
        s, body = call(t["access_token"], tool, path="ABOUT.md", query="CANARY")
        check("error" in body and s == 200, f"Call non-existent tool {tool}", f"-> {str(body.get('error'))[:60]}")
    for method in ("resources/list", "resources/read", "prompts/list", "completion/complete", "logging/setLevel", "sampling/createMessage"):
        s, b, _ = rpc(t["access_token"], method, {"uri": "file:///etc/passwd"})
        check("error" in json.loads(b), f"MCP method {method}", b[:60])


def t_read_tools_do_not_leak():
    _, t = grant()
    for tool in ("get_today", "list_projects"):
        s, body = call(t["access_token"], tool)
        text = json.dumps(body)
        hit = leaked(text)
        check(hit is None, f"{tool} output contains no canary or project body text", hit or "clean")
    s, body = call(t["access_token"], "get_today", path="../../outside.md")
    check(body.get("result", {}).get("isError") is True, "get_today rejects unexpected arguments")


def corpus_snapshot() -> dict:
    snap = {}
    for p in sb.USER_ROOT.rglob("*.md"):
        if ".git" in p.parts or "db" in p.relative_to(sb.USER_ROOT).parts[:1]:
            continue
        snap[str(p)] = p.read_text(encoding="utf-8", errors="replace")
    snap["outside"] = (sb.SANDBOX / "outside.md").read_text(encoding="utf-8")
    snap["sibling"] = (sb.DATA_ROOT / "sibling.md").read_text(encoding="utf-8")
    return snap


def t_write_tool_abuse():
    _, t = grant()
    tok = t["access_token"]
    before = corpus_snapshot()

    # --- project targeting: paths a hostile prompt might try
    for project in ("../outside.md", "../../.sandbox/outside.md", "..\\..\\outside.md", "/etc/passwd",
                    "ABOUT.md", "inbox.md", "VIT/Daily/x.md", "VIT/../ABOUT.md", "VIT/db/leak.md",
                    "VIT/secret-project.md\x00.png", "db/leak.md", "VIT//secret-project.md", "./VIT/secret-project.md"):
        s, body = call(tok, "add_task", task="pwn", project=project)
        check(body.get("result", {}).get("isError") is True, f"add_task project={project!r}")

    # --- toggling lines that are NOT on today's list
    for rel, text in (("VIT/secret-project.md", "do the thing"), ("inbox.md", "sandbox inbox item"),
                      ("../outside.md", sb.CANARY_OUT), ("../../.sandbox/outside.md", sb.CANARY_OUT),
                      ("VIT/Daily/../secret-project.md", "do the thing"), ("ABOUT.md", "anything"),
                      ("/etc/passwd", "root")):
        s, body = call(tok, "toggle_task", rel_path=rel, exact_text=text)
        check(body.get("result", {}).get("isError") is True, f"toggle_task {rel!r}")

    # --- injection through task / capture text
    payloads = ["x\n\n## Tasks\n- [x] forged done item", "x\r\n# New heading\r\n- [ ] injected",
                "close <!-- --> then --> more", "tag ^p:deadbeef", "priority:high due:2020-01-01 spoof",
                "see VIT/secret-project.md now", "<script>alert(1)</script>"]
    for pl in payloads:
        call(tok, "add_task", task=pl, project="VIT/secret-project.md")
        call(tok, "capture", text=pl)
    daily = next(p for p in (sb.USER_ROOT / "VIT" / "Daily").glob("*.md"))
    d_text, i_text = daily.read_text(encoding="utf-8"), (sb.USER_ROOT / "inbox.md").read_text(encoding="utf-8")
    heading_lines = [ln for txt in (d_text, i_text) for ln in txt.splitlines() if ln.startswith("# New") or ln.startswith("## Tasks") and txt is i_text]
    check(not heading_lines, "Newline-injected headings never reach the corpus as their own line", str(heading_lines[:2]))
    check("forged done item" not in "\n".join(ln for ln in d_text.splitlines() if ln.startswith("- [x]")),
          "Forged '- [x]' line cannot be planted as its own task line")
    check("^p:deadbeef" not in d_text and "priority:high due:2020" not in d_text, "Parser tag syntax refused in task text")
    stray = [ln for ln in i_text.splitlines() if ln.count("<!--") > 1 or ln.count("-->") > 1]
    check(not stray, "Comment markers stripped from captured text", str(stray[:1]))

    # --- nothing outside the intended files changed
    after = corpus_snapshot()
    changed = sorted(k for k in after if before.get(k) != after[k])
    allowed = {str(daily), str(sb.USER_ROOT / "inbox.md")}
    unexpected = [c for c in changed if c not in allowed]
    check(not unexpected, "Only today's Daily file and inbox.md were ever modified", ", ".join(unexpected))
    check(after["outside"] == before["outside"] and after["sibling"] == before["sibling"],
          "Files outside the data root untouched")
    check("- [ ] do the thing" in after[str(sb.USER_ROOT / "VIT" / "secret-project.md")],
          "Project backlog line not toggled through the connector")


def t_argument_fuzz():
    _, t = grant()
    tok = t["access_token"]
    weird = [None, 5, True, [], {}, ["a"], {"a": 1}, "x" * 100000, "‮" * 50, "\x00", "😀" * 400]
    bad_status = []
    for tool in ("add_task", "capture", "toggle_task", "get_today", "list_projects"):
        for w in weird:
            for args in ({"task": w, "project": w, "text": w, "rel_path": w, "exact_text": w, "priority": w, "due": w}, w):
                s, b, _ = rpc(tok, "tools/call", {"name": tool, "arguments": args})
                if s >= 500:
                    bad_status.append(f"{tool}:{type(w).__name__} -> {s}")
    check(not bad_status, "Hostile argument shapes never cause a 500", "; ".join(bad_status[:3]))
    for raw in (b"{", b"[", b"null", b'"str"', b"[[[[[[[[[[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]]]]]]]]]", b"{" * 5000,
                json.dumps({"jsonrpc": "2.0", "id": {"x": [1] * 1000}, "method": "tools/list"}).encode(),
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": ["x"]}).encode(),
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": "str"}).encode(),
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": ["x"]}}).encode()):
        s, _, _ = http("POST", "/mcp", headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}, raw=raw)
        check(s < 500, f"Malformed JSON-RPC {raw[:24]!r}", f"status {s}")


def t_batch_amplification():
    _, t = grant()
    batch = [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": "get_today", "arguments": {}}} for i in range(5000)]
    s, _, _ = http("POST", "/mcp", headers={"Authorization": f"Bearer {t['access_token']}"}, json_body=batch)
    check(s == 400, "5000-call JSON-RPC batch refused before any tool runs", f"status {s}")


# ---------------------------------------------------------------------------
# 4. Consent page, discovery, storage
# ---------------------------------------------------------------------------

def t_consent_page():
    cid = new_client('<script>alert(document.domain)</script><img src=x onerror=alert(1)>"\'>')
    _, challenge = pkce()
    s, b, h = http("GET", "/oauth/authorize?" + urllib.parse.urlencode(auth_form(cid, challenge, state='"><script>alert(1)</script>')))
    check("<script>alert" not in b and "<img src=x" not in b, "Hostile client_name is escaped on the consent page", f"status {s}")
    check(h.get("X-Frame-Options") == "DENY" and "frame-ancestors 'none'" in h.get("Content-Security-Policy", ""),
          "Consent page cannot be framed (clickjacking)")
    check("no-store" in h.get("Cache-Control", ""), "Consent page is not cacheable")
    check("<script" not in b.lower().replace("&lt;script", ""), "Consent page carries no script of its own")
    s, b, _ = http("POST", "/oauth/authorize", form={**auth_form(cid, challenge, state='"><script>alert(1)</script>'),
                                                      "username": "owner", "password": sb.USERS["owner"][0], "action": "deny"})
    target = refresh_target(b) or ""
    check("<script" not in b.lower() and "%3Cscript" in target.replace("%3c", "%3C"),
          "Hostile state value is URL-encoded in the redirect, not echoed raw")


def t_discovery():
    s, b, _ = http("GET", "/.well-known/oauth-authorization-server", headers={"Host": "evil.example"})
    check(s == 200 and "evil.example" not in b, "Discovery document ignores a spoofed Host header")
    meta = json.loads(b)
    check(meta["code_challenge_methods_supported"] == ["S256"] and meta["token_endpoint_auth_methods_supported"] == ["none"],
          "Discovery advertises S256 only and no client secrets")
    check(all(v.startswith(sb.PUBLIC_URL) for k, v in meta.items() if k.endswith("_endpoint")), "All endpoints on the configured base URL")
    s, b, _ = http("GET", "/.well-known/acme-challenge/test")
    check(s in (401, 404), "Arbitrary /.well-known path does not open anything", f"status {s}")
    for p in ("/oauth/../api/today", "/oauth/%2e%2e/api/today", "/.well-known/../api/today", "/mcp/../api/today"):
        s, b, _ = http("GET", p)
        check(s in (401, 404) and "tasks" not in b, f"Path trick {p} does not bypass auth", f"status {s}")


def t_registration_abuse():
    ip = "198.51.100.77"
    codes = [register(ip=ip)[0] for _ in range(14)]
    check(429 in codes, "Open registration is rate-limited per IP", f"statuses {sorted(set(codes))}")
    s, _, _ = http("POST", "/oauth/register", raw=b"not json", headers={"Content-Type": "application/json"})
    check(s == 400, "Registration with garbage body", f"status {s}")
    s, b, _ = register(name="A" * 10000 + "\x00\x1b[31m")
    stored = json.loads(b).get("client_name", "") if s == 201 else ""
    check(s != 500 and len(stored) <= 100 and "\x00" not in stored and "\x1b" not in stored,
          "Oversized / control-character client_name is bounded and cleaned", f"status {s}, stored {len(stored)} chars")


def t_database_hygiene():
    cid, t = grant()
    conn = sqlite3.connect(str(sb.DB_PATH))
    try:
        for label, raw in (("access token", t["access_token"]), ("refresh token", t["refresh_token"])):
            n = conn.execute("SELECT COUNT(*) FROM oauth_tokens WHERE token_hash = ?", (raw,)).fetchone()[0]
            check(n == 0, f"Raw {label} is not stored in the database")
        dump = "\n".join(str(r) for tbl in ("oauth_tokens", "oauth_codes", "oauth_clients") for r in conn.execute(f"SELECT * FROM {tbl}"))
        check(t["access_token"] not in dump and t["refresh_token"] not in dump, "No raw credential anywhere in oauth_* tables")
        n = conn.execute("SELECT COUNT(*) FROM oauth_codes WHERE code_hash LIKE 'slc\\_%' ESCAPE '\\'").fetchone()[0]
        check(n == 0, "Raw authorization codes are not stored")
    finally:
        conn.close()


def t_revocation():
    cid, t = grant()
    s, _, _ = http("POST", "/oauth/revoke", form={"token": t["refresh_token"]})
    a, _, _ = rpc(t["access_token"], "tools/list")
    r, _, _ = http("POST", "/oauth/token", form={"grant_type": "refresh_token", "client_id": cid, "refresh_token": t["refresh_token"]})
    check(s == 200 and a == 401 and r == 400, "Revoking one token ends the whole grant", f"revoke {s}, access {a}, refresh {r}")
    s, _, _ = http("POST", "/oauth/revoke", form={"token": "garbage"})
    check(s == 200, "Revoking an unknown token is silent (no oracle)")


# ---------------------------------------------------------------------------
# 5. Last: things that burn shared budgets / lock accounts
# ---------------------------------------------------------------------------

def t_password_guessing_on_consent_page():
    cid = new_client()
    _, challenge = pkce()
    ip = f"198.51.100.{random.randint(1, 254)}"   # isolate the lockout to one address
    codes = [approve(cid, challenge, user="limited", password=f"wrong-{i}", ip=ip)[0] for i in range(6)]
    s, b, _ = approve(cid, challenge, user="limited", ip=ip)       # now the CORRECT password
    check(codes[:5] == [401] * 5 and s == 429 and code_from(b) is None,
          "Five wrong passwords lock the consent page (correct password then refused)", f"{codes} then {s}")
    s, _, _ = http("POST", "/api/auth/login", json_body={"username": "limited", "password": sb.USERS["limited"][0]}, ip=ip)
    check(s == 429, "...and the same lockout covers the normal login from that address", f"status {s}")
    s, b, _ = approve(cid, challenge, user="nosuchuser", password="x" * 12)
    check(s == 401 and "Invalid credentials" in b, "Unknown user is indistinguishable from wrong password")


def t_write_flood():
    _, t = grant()
    statuses = []
    for i in range(70):
        s, body = call(t["access_token"], "capture", text=f"flood {i}")
        statuses.append(body.get("result", {}).get("isError"))
    check(True in statuses, "Hourly write budget stops a capture flood", f"{statuses.count(False)} accepted, {statuses.count(True)} refused")


def main():
    s, _, _ = http("GET", "/healthz")
    if s != 200:
        sys.exit("sandbox not running — start tooling/sandbox/run_sandbox.py first")
    s, _, _ = http("GET", "/.well-known/oauth-authorization-server")
    if s != 200:
        sys.exit("connector is not enabled in the sandbox (MCP_ENABLED) — check sandbox_env.py")

    t_registration_redirects()
    t_authorize_redirect_tampering()
    t_pkce_required()
    t_code_theft()
    t_code_replay()
    t_refresh_attacks()
    t_token_confusion()
    t_no_token_anywhere()
    t_origin()
    t_scope_enforcement()
    t_tool_surface()
    t_read_tools_do_not_leak()
    t_write_tool_abuse()
    t_argument_fuzz()
    t_batch_amplification()
    t_consent_page()
    t_discovery()
    t_database_hygiene()
    t_revocation()
    t_registration_abuse()
    t_password_guessing_on_consent_page()   # locks an address
    t_write_flood()                         # spends the hourly write budget

    vulns = sum(1 for r in RESULTS if r[0] == "VULN")
    print(f"\n{len(RESULTS)} checks, {vulns} VULN")
    sys.exit(vulns)


if __name__ == "__main__":
    main()
