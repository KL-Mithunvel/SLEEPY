"""
Attack sandbox — shared environment setup.

Import this BEFORE any backend module. It points every data path at a
throwaway `.sandbox/` folder at the repo root, blanks every real credential
(so nothing can reach Anthropic, O365, GitHub, or the real data/klm corpus),
and runs the app in production mode (real JWT auth, no dev bypass) — the same
posture an attacker faces on klm.smtw.in, minus nginx.

Canary strings are planted inside the corpus (should be readable only with a
valid token) and outside it (should never be readable at all). Any attack
output containing an OUTSIDE canary is a confirmed boundary break.
"""

import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "code" / "backend"
SANDBOX = REPO_ROOT / ".sandbox"
DATA_ROOT = SANDBOX / "data"
USER_ROOT = DATA_ROOT / "klm"
DB_PATH = USER_ROOT / "db" / "sqlite" / "pma.db"

HOST, PORT = "127.0.0.1", 5055
BASE_URL = f"http://{HOST}:{PORT}"

# Known accounts. Passwords are sandbox-only.
USERS = {
    "owner":   ("Owner-Sandbox-Pass-1", "admin"),
    "limited": ("Limited-Sandbox-Pass-1", "user"),
}

CANARY_IN = "CANARY-INSIDE-CORPUS-7f3a"      # legit data — needs a token
CANARY_OUT = "CANARY-OUTSIDE-ROOT-91bd"      # must NEVER come back from any API
CANARY_DB = "CANARY-DB-DIR-44c2"             # lives under db/ — never readable
CANARY_SRC = "CANARY-BACKEND-SECRET-e810"    # stands in for secrets_app.py


def _secret_key() -> str:
    """Stable per-sandbox signing key (so tokens survive a server restart)."""
    p = SANDBOX / "auth_secret"
    if not p.exists():
        SANDBOX.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_hex(32))
    return p.read_text().strip()


def apply_env() -> None:
    env = {
        "APP_ENV": "production",
        "DEV_AUTH_BYPASS": "0",
        "AUTH_SECRET_KEY": _secret_key(),
        "AUTH_TOKEN_TTL_MINUTES": "20",
        # Fake key: passes the prod boot guard, fails at Anthropic — no spend.
        "ANTHROPIC_API_KEY": "sk-ant-sandbox-not-a-real-key",
        "CLAUDE_API_KEY": "sk-ant-sandbox-not-a-real-key",
        "O365_TENANT_ID": "", "O365_CLIENT_ID": "", "O365_CLIENT_SECRET": "",
        "O365_MAILBOX": "",
        "USER_EMAIL": "owner@smtw.in",
        "ALERT_EMAIL": "", "ALERTS_ENABLED": "0",
        "OFFSITE_PUSH_ENABLED": "0", "OFFSITE_SNAPSHOT_DIR": "",
        "DATA_ROOT": str(DATA_ROOT),
        "USER_DATA_ROOT": str(USER_ROOT),
        "SQLITE_DB_PATH": str(DB_PATH),
        "CHROMA_HOST": "",
        "CHROMA_PATH": str(USER_ROOT / "db" / "chroma"),
        "GEOIP_DB_PATH": str(SANDBOX / "no-geoip.mmdb"),
        "STOP_SENTINEL_PATH": str(SANDBOX / ".stop"),
        "PMA_NEWS_WATCH_CRON_DISABLED": "1",
        "CORS_ORIGINS": "http://localhost:5173",
        "USER_NICK": "KLM",
    }
    os.environ.update(env)
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def seed(reset: bool = False) -> None:
    """Build the fake corpus + canaries. reset=True wipes .sandbox/ first."""
    if reset and SANDBOX.exists():
        key = SANDBOX / "auth_secret"
        saved = key.read_text() if key.exists() else None
        shutil.rmtree(SANDBOX, ignore_errors=True)
        if saved:
            SANDBOX.mkdir(parents=True, exist_ok=True)
            key.write_text(saved)

    # Outside the data root — traversal targets.
    _w(SANDBOX / "outside.md", f"# outside\n{CANARY_OUT}\n- [ ] {CANARY_OUT}\n")
    _w(DATA_ROOT / "sibling.md", f"# sibling\n{CANARY_OUT}\n- [ ] {CANARY_OUT}\n")
    _w(SANDBOX / "secrets_app.py", f'AUTH_SECRET_KEY = "{CANARY_SRC}"\n')

    import datetime as _dt
    today = _dt.date.today().isoformat()
    files = {
        USER_ROOT / "ABOUT.md": "# About\nSandbox owner.\n",
        USER_ROOT / "inbox.md": "# Inbox\n\n## Captured\n- [ ] sandbox inbox item\n",
        USER_ROOT / "VIT" / "secret-project.md":
            "---\ntitle: Secret Project\nstatus: active\n---\n# Secret Project\n\n"
            f"## Goal\nConfidential: {CANARY_IN}\n\n## Tasks\n- [ ] do the thing\n",
        USER_ROOT / "VIT" / "Daily" / f"{today}.md":
            f"---\ndate: {today}\n---\n# Daily\n\n## Tasks\n- [ ] daily task one\n",
        USER_ROOT / "db" / "leak.md": f"# internal\n{CANARY_DB}\n",
        USER_ROOT / ".gitignore": "db/\n*.db\n",
    }
    # Self-repairing: restore any seed file an attack run deleted or clobbered
    # the canary out of, so every run starts from a known corpus.
    for path, text in files.items():
        if not path.exists():
            _w(path, text)
    secret = USER_ROOT / "VIT" / "secret-project.md"
    if CANARY_IN not in secret.read_text(encoding="utf-8"):
        _w(secret, files[secret])
    if (USER_ROOT / ".git").exists():
        return

    env = {**os.environ, "GIT_AUTHOR_NAME": "sleepy", "GIT_AUTHOR_EMAIL": "sleepy@smtw.in",
           "GIT_COMMITTER_NAME": "sleepy", "GIT_COMMITTER_EMAIL": "sleepy@smtw.in"}
    subprocess.run(["git", "init", "-q"], cwd=USER_ROOT, check=True)
    subprocess.run(["git", "add", "-A"], cwd=USER_ROOT, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "sandbox seed"], cwd=USER_ROOT, env=env, check=True)


def create_users() -> None:
    import auth_utils
    import local_db
    local_db.init_db()
    conn = local_db.get_db()
    try:
        for name, (pw, role) in USERS.items():
            conn.execute(
                "INSERT OR REPLACE INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (name, auth_utils.hash_password(pw), role),
            )
        conn.commit()
    finally:
        local_db.return_db(conn)
