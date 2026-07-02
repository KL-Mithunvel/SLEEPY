"""
SLEEPY system diagnostic -- run with: uv run python tooling/diagnose.py
Checks every component independently and prints a clear pass/fail for each.
"""
# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code", "backend"))
os.environ.setdefault("DEV_AUTH_BYPASS", "1")

PASS = "  OK "
FAIL = " FAIL"
WARN = " WARN"
SKIP = " SKIP"

issues = []

def ok(label):
    print(f"[{PASS}] {label}")

def fail(label, reason=""):
    print(f"[{FAIL}] {label}" + (f"\n       -> {reason}" if reason else ""))
    issues.append(label)

def warn(label, reason=""):
    print(f"[{WARN}] {label}" + (f"\n       -> {reason}" if reason else ""))

def skip(label, reason=""):
    print(f"[{SKIP}] {label}" + (f"  ({reason})" if reason else ""))

SEP  = "=" * 44
SEP2 = "-" * 44

print(f"\n{SEP}")
print("  SLEEPY Diagnostic")
print(f"{SEP}\n")

# ---------------------------------------------------------------------------
print("1. Config & Secrets")
print(SEP2)

try:
    import config
    ok("config.py imports")
except Exception as e:
    fail("config.py imports", str(e))
    sys.exit(1)

if config.ANTHROPIC_API_KEY:
    ok(f"ANTHROPIC_API_KEY set (prefix: {config.ANTHROPIC_API_KEY[:12]}...)")
elif config.ANTHROPIC_AUTH_TOKEN:
    ok(f"Claude Code OAuth token loaded (prefix: {config.ANTHROPIC_AUTH_TOKEN[:20]}...)")
else:
    fail(
        "No Anthropic credentials",
        "Set CLAUDE_API_KEY in secrets_app.py  OR  log in to Claude Code (run: claude)"
    )

if config.DEV_AUTH_BYPASS:
    ok("DEV_AUTH_BYPASS=1  (dev mode -- no Keycloak needed)")
elif config.KEYCLOAK_PUBLIC_URL and config.KEYCLOAK_REALM:
    ok(f"Keycloak: {config.KEYCLOAK_PUBLIC_URL} / {config.KEYCLOAK_REALM}")
else:
    warn(
        "Keycloak not configured",
        "Set KEYCLOAK_PUBLIC_URL + KEYCLOAK_REALM in secrets_app.py for prod auth"
    )

# ---------------------------------------------------------------------------
print(f"\n2. Data Directory")
print(SEP2)

data_root = config.USER_DATA_ROOT

if os.path.isdir(data_root):
    ok(f"USER_DATA_ROOT exists: {data_root}")
else:
    fail("USER_DATA_ROOT missing", f"mkdir {data_root}")

for sub in ["db/sqlite", "db/chroma", "logs"]:
    p = os.path.join(data_root, sub.replace("/", os.sep))
    if os.path.isdir(p):
        ok(f"  {sub}/")
    else:
        warn(f"  {sub}/ missing", f"mkdir {p}")

for fname in ["inbox.md", "ABOUT.md"]:
    fp = os.path.join(data_root, fname)
    if os.path.isfile(fp):
        ok(f"  {fname}")
    else:
        warn(f"  {fname} missing", f"create an empty file at {fp}")

try:
    import git
    try:
        repo = git.Repo(data_root, search_parent_directories=False)
        ok(f"  corpus git repo OK  (HEAD: {repo.head.commit.hexsha[:8]})")
    except git.InvalidGitRepositoryError:
        warn("  corpus is not a git repo", f"cd {data_root} && git init")
    except Exception as e:
        warn("  corpus git check", str(e)[:80])
except ImportError:
    skip("  git repo check", "gitpython not installed")

# ---------------------------------------------------------------------------
print(f"\n3. SQLite Database")
print(SEP2)

try:
    import local_db
    local_db.init_db()
    conn = local_db.get_db()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    expected = {"db_version", "ai_events", "task_queue", "md_chunks_meta"}
    missing = expected - set(tables)
    if not missing:
        ok(f"Schema OK -- tables: {', '.join(sorted(tables))}")
        row = conn.execute("SELECT MAX(version) FROM db_version").fetchone()
        ok(f"Migration version: {row[0]}")
    else:
        fail("Schema incomplete", f"missing: {missing}")
    local_db.return_db(conn)
except Exception as e:
    fail("SQLite", str(e)[:120])

# ---------------------------------------------------------------------------
print(f"\n4. ChromaDB / Vector Index")
print(SEP2)

try:
    import md_indexer
    col = md_indexer._get_collection()
    count = col.count()
    if count > 0:
        ok(f"ChromaDB reachable -- {count} chunks indexed")
    else:
        warn(
            "ChromaDB reachable but 0 chunks indexed",
            "Run: tooling\\run-md-index.bat"
        )
except Exception as e:
    msg = str(e)
    if "refused" in msg.lower() or "connect" in msg.lower():
        fail(
            "ChromaDB not reachable",
            "Start it: docker compose -f docker-compose.dev.yml up -d"
        )
    else:
        warn("ChromaDB", msg[:120])

# ---------------------------------------------------------------------------
print(f"\n5. LLM (Anthropic API)")
print(SEP2)

has_creds = config.ANTHROPIC_API_KEY or config.ANTHROPIC_AUTH_TOKEN
if not has_creds:
    skip("Anthropic API call", "no credentials -- fix step 1 first")
else:
    mode = config.ANTHROPIC_AUTH_MODE
    try:
        import anthropic
        if config.ANTHROPIC_API_KEY:
            client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        else:
            client = anthropic.Anthropic(auth_token=config.ANTHROPIC_AUTH_TOKEN)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=16,
            messages=[{"role": "user", "content": "reply with the single word: ok"}],
        )
        reply = msg.content[0].text.strip()
        ok(f"Anthropic API call succeeded [{mode}] -- reply: '{reply}'")
    except anthropic.AuthenticationError:
        if mode == "oauth":
            fail("Claude Code token expired",
                 "Run any `claude` command in your terminal to refresh, then retry")
        else:
            fail("Anthropic API key invalid", "Check the key at console.anthropic.com")
    except anthropic.RateLimitError:
        warn("Rate limit hit", "Credentials are valid but you are rate-limited")
    except Exception as e:
        fail("Anthropic API", str(e)[:120])

# ---------------------------------------------------------------------------
print(f"\n6. Source Files (prompts, skills)")
print(SEP2)

sp = config.SYSTEM_PROMPT_PATH
if sp.exists():
    ok(f"SystemPrompt.MD  ({sp.stat().st_size} bytes)")
else:
    warn("SystemPrompt.MD missing", str(sp))

try:
    import skills
    manifest = skills.get_manifest()
    count = manifest.count("\n") if manifest else 0
    ok(f"Skills manifest loaded  ({count} lines)")
except Exception as e:
    warn("Skills", str(e)[:80])

skills_dir = config.SKILLS_DIR
if skills_dir.exists():
    md_files = list(skills_dir.glob("*.md"))
    ok(f"Skills dir: {len(md_files)} skill file(s) -- {[f.stem for f in md_files]}")
else:
    warn("Skills directory missing", str(skills_dir))

# ---------------------------------------------------------------------------
print(f"\n7. Flask App")
print(SEP2)

try:
    from app import app as flask_app
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        r = c.get("/healthz")
        if r.status_code == 200:
            ok("Flask app starts and /healthz returns 200")
        else:
            fail("/healthz", f"status {r.status_code}")
except Exception as e:
    fail("Flask app", str(e)[:120])

try:
    blueprints = list(flask_app.blueprints.keys())
    ok(f"Blueprints registered: {', '.join(sorted(blueprints))}")
except Exception as e:
    warn("Blueprint check", str(e)[:80])

# ---------------------------------------------------------------------------
print(f"\n8. Task Queue & Worker")
print(SEP2)

try:
    import task_handlers
    registered = list(task_handlers.HANDLERS.keys())
    ok(f"{len(registered)} handlers registered:")
    for h in sorted(registered):
        print(f"         - {h}")
except Exception as e:
    fail("task_handlers", str(e)[:120])

try:
    import scheduled_tasks
    jobs = scheduled_tasks.SCHEDULED_TASKS
    enabled = [j["task_type"] for j in jobs if j.get("enabled", True)]
    disabled = [j["task_type"] for j in jobs if not j.get("enabled", True)]
    ok(f"{len(enabled)} scheduled jobs enabled: {', '.join(enabled)}")
    if disabled:
        skip(f"{len(disabled)} disabled: {', '.join(disabled)}")
except Exception as e:
    fail("scheduled_tasks", str(e)[:120])

# ---------------------------------------------------------------------------
print(f"\n9. Integrations")
print(SEP2)

integrations_status = [
    ("Email (O365)", bool(config.O365_CLIENT_ID and config.O365_CLIENT_SECRET
                          and config.O365_TENANT_ID and config.O365_MAILBOX),
     "set O365_* fields -- see docs/SETUP.md Part 5"),
]

for name, configured, hint in integrations_status:
    if configured:
        ok(f"{name} configured")
    else:
        skip(f"{name} not configured", hint)

# ---------------------------------------------------------------------------
print(f"\n{SEP}")
if issues:
    print(f"  {len(issues)} problem(s) found:")
    for i in issues:
        print(f"    [X]  {i}")
    print(f"\n  -> See docs/SETUP.md for fix instructions.")
else:
    print("  All checks passed -- system is healthy.")
print(f"{SEP}\n")
