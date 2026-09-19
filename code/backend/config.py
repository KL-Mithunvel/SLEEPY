import os
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Safe import of secrets_app — file may be absent in some deploy modes
# ---------------------------------------------------------------------------
try:
    import secrets_app as _secrets_app
except ImportError:
    _secrets_app = None


def _get(key: str, default=None):
    """
    3-tier config lookup: env var → secrets_app.py attribute → default.
    Env vars always win; values keep the type of the default when cast is needed.
    """
    if key in os.environ:
        return os.environ[key]
    if _secrets_app is not None and hasattr(_secrets_app, key):
        return getattr(_secrets_app, key)
    return default


# ---------------------------------------------------------------------------
# Computed path constants (resolved from this file's location at import time)
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent   # code/backend/
_CODE_DIR    = _BACKEND_DIR.parent               # code/
_REPO_ROOT   = _CODE_DIR.parent                  # repo root

SRC_ROOT          = _CODE_DIR / "src"                         # code/src/
PROMPTS_DIR       = SRC_ROOT / "prompts"                      # code/src/prompts/
SKILLS_DIR        = PROMPTS_DIR / "skills"                    # code/src/prompts/skills/
HELP_ROOT         = SRC_ROOT / "help"                         # code/src/help/
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "SystemPrompt.MD"          # hot-reloaded per request

# ---------------------------------------------------------------------------
# AI / LLM
# ---------------------------------------------------------------------------
# One credential for every LLM path (interactive chat via the Anthropic SDK,
# background jobs via LiteLLM, news watch via the Batches API): a real API key.
# The old fallback that borrowed the personal Claude Code OAuth token from
# ~/.claude/.credentials.json is gone — it was outside Claude Code's terms of
# use, and only the chat path honoured it anyway, so briefings/goal planning/
# news watch silently failed in dev whenever it was in effect.
ANTHROPIC_API_KEY: str = _get("ANTHROPIC_API_KEY") or _get("CLAUDE_API_KEY", "")
CLAUDE_API_KEY: str = ANTHROPIC_API_KEY  # backward-compat alias

LLM_DEFAULT_MODEL: str    = _get("LLM_DEFAULT_MODEL", "claude-sonnet-4-6")
LLM_MAX_CONTEXT_CHUNKS: int = int(_get("LLM_MAX_CONTEXT_CHUNKS", 8))
LLM_MAX_TOKENS: int         = int(_get("LLM_MAX_TOKENS", 4096))

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
DEV_AUTH_BYPASS: bool = str(_get("DEV_AUTH_BYPASS", "0")).strip() in ("1", "true", "yes")
DEV_USER: str         = _get("DEV_USER", "klm")

# In-app auth — self-issued JWT (HS256), no external identity provider.
# AUTH_SECRET_KEY must be set for real (never blank) in prod: generate via
# `python -c "import secrets; print(secrets.token_hex(32))"`.
AUTH_SECRET_KEY: str     = _get("AUTH_SECRET_KEY", "")
AUTH_TOKEN_TTL_DAYS: int = int(_get("AUTH_TOKEN_TTL_DAYS", 7))

# DB-IP City Lite .mmdb, downloaded at Docker build time (see
# Dockerfile.backend) — not present in local dev, geoip_lookup.py handles
# that gracefully.
GEOIP_DB_PATH: str = _get("GEOIP_DB_PATH", str(_REPO_ROOT / "geoip" / "dbip-city-lite.mmdb"))

# Explicit prod flag. Must be set to "production" explicitly (docker-compose.yml
# does this) for the prod-only hard-fail guards below to engage.
APP_ENV: str = str(_get("APP_ENV", "development")).strip().lower()
IS_PROD: bool = APP_ENV in ("prod", "production")

if IS_PROD and DEV_AUTH_BYPASS:
    raise RuntimeError(
        "APP_ENV=production with DEV_AUTH_BYPASS enabled — refusing to start. "
        "This combination exposes the app with owner rights to anyone, unauthenticated."
    )

if IS_PROD and not ANTHROPIC_API_KEY:
    raise RuntimeError(
        "APP_ENV=production but no ANTHROPIC_API_KEY is set — refusing to start. "
        "Set ANTHROPIC_API_KEY in secrets_app.py or the environment."
    )

# Independent of APP_ENV: whenever real logins are in play (bypass off), a blank
# signing key must never be accepted. PyJWT refuses an empty HMAC key, so this
# would otherwise surface as every login 500ing rather than as a clear message
# — and a forgotten APP_ENV must not be the only thing standing between a
# misconfigured box and a broken auth layer.
if not DEV_AUTH_BYPASS and not AUTH_SECRET_KEY:
    raise RuntimeError(
        "DEV_AUTH_BYPASS is off but AUTH_SECRET_KEY is blank — refusing to start. Generate one "
        "via: python -c \"import secrets; print(secrets.token_hex(32))\" and set it in "
        "secrets_app.py (or set DEV_AUTH_BYPASS=1 for local development)."
    )
if AUTH_SECRET_KEY and len(AUTH_SECRET_KEY) < 32:
    raise RuntimeError(
        "AUTH_SECRET_KEY is too short (< 32 chars) to be a safe HS256 signing key. Generate one "
        "via: python -c \"import secrets; print(secrets.token_hex(32))\""
    )

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
SQLITE_DB_PATH: str = _get("SQLITE_DB_PATH", "../../data/klm/db/sqlite/pma.db")

# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
DEBUG: bool        = os.getenv("DEBUG", "0") == "1"
CORS_ORIGINS: list = _get("CORS_ORIGINS", "http://localhost:5173").split(",")

# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------
SLOW_REQUEST_MS: int = int(_get("SLOW_REQUEST_MS", 3000))

# ---------------------------------------------------------------------------
# Data roots
# ---------------------------------------------------------------------------
# DATA_ROOT is the base for ALL user data: DATA_ROOT/<username>/
DATA_ROOT: Path = Path(str(_get("DATA_ROOT", str(_REPO_ROOT / "data"))))

# Legacy single-user shortcut — kept for backward compat (used by ai_bp.py etc.)
USER_DATA_ROOT: str = str(_get("USER_DATA_ROOT", str(_REPO_ROOT / "data" / "klm")))

# ---------------------------------------------------------------------------
# ChromaDB
# ---------------------------------------------------------------------------
CHROMA_HOST: str       = _get("CHROMA_HOST", "")
CHROMA_PORT: int       = int(_get("CHROMA_PORT", 8001))
CHROMA_PATH: str       = str(_get("CHROMA_PATH", str(_REPO_ROOT / "data" / "klm" / "db" / "chroma")))
CHROMA_COLLECTION: str = _get("CHROMA_COLLECTION", "md_corpus")

# ---------------------------------------------------------------------------
# Embedding model
# ---------------------------------------------------------------------------
EMBED_MODEL: str = _get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")

# ---------------------------------------------------------------------------
# Integrations — Office 365 email (MSAL + Graph API)
# ---------------------------------------------------------------------------
O365_TENANT_ID: str    = _get("O365_TENANT_ID", "")
O365_CLIENT_ID: str    = _get("O365_CLIENT_ID", "")
O365_CLIENT_SECRET: str = _get("O365_CLIENT_SECRET", "")
O365_MAILBOX: str      = _get("O365_MAILBOX", "")
O365_SENDER_NAME: str  = _get("O365_SENDER_NAME", "PMA Bot")

# Destination address for scheduled digests (morning briefing + deadlines) — distinct
# from O365_MAILBOX, which is the bot's send-from mailbox.
USER_EMAIL: str = _get("USER_EMAIL", "")

# ---------------------------------------------------------------------------
# System alerting (alerts.py)
# ---------------------------------------------------------------------------
# Internal failures used to die silently in SQLite: a task that burned through
# max_attempts just sat there as status='failed' with nobody told. These route
# such events to an email the owner actually reads, throttled per alert key so
# a persistently broken job can't turn into an hourly mail flood.
ALERTS_ENABLED: bool      = str(_get("ALERTS_ENABLED", "1")).strip() in ("1", "true", "yes")
ALERT_EMAIL: str          = _get("ALERT_EMAIL", "") or USER_EMAIL
ALERT_COOLDOWN_HOURS: int = int(_get("ALERT_COOLDOWN_HOURS", 6))

# ---------------------------------------------------------------------------
# Worker / indexing
# ---------------------------------------------------------------------------
INDEX_SYNC_INTERVAL_SEC: int = int(_get("INDEX_SYNC_INTERVAL_SEC", 300))

# Cooperative stop signal. Windows has no usable SIGTERM for a child process
# (Popen.terminate() is a hard TerminateProcess, so no handler ever runs), so
# the dev stop script drops this file and both main.py and worker.py notice it
# and shut down cleanly. On Linux/Docker the SIGTERM handler does the same job.
STOP_SENTINEL_PATH: str = str(_get("STOP_SENTINEL_PATH", str(_REPO_ROOT / ".sleepy-stop")))

# When "1", skip registering the midnight news-watch cron (manual triggers still work)
NEWS_WATCH_CRON_DISABLED: bool = str(_get("PMA_NEWS_WATCH_CRON_DISABLED", "0")).strip() in ("1", "true", "yes")

# When "1" (default), news watch runs all active projects every day instead of the
# day-of-week rotation — the rotation left the feed silent for weeks when a night's
# single scheduled project had nothing new. Set to "0" to bring the rotation back
# (e.g. to cut Batches API volume) once the corpus has many more active projects.
NEWS_RUN_ALL: bool = str(_get("PMA_NEWS_RUN_ALL", "1")).strip() in ("1", "true", "yes")

# Projects edited within this many days count as "recently active" (→ 5 items/topic)
RECENCY_DAYS: int = int(_get("RECENCY_DAYS", 14))

# Anthropic SDK retry count for batch-create and LLM-dedup calls
NEWS_MAX_RETRIES: int = int(_get("NEWS_MAX_RETRIES", 8))

# Standalone NewsWatch.md topics with no recent +1 feedback past this many days from
# their `added` date are treated as dormant (breakthrough-only, budget=1).
NEWS_TOPIC_DORMANT_DAYS: int = int(_get("NEWS_TOPIC_DORMANT_DAYS", 30))

# An unclicked news item may resurface in inbox.md up to this many times before
# being permanently excluded. Clicking excludes it immediately regardless of count.
NEWS_MAX_RESHOW: int = int(_get("NEWS_MAX_RESHOW", 3))

# Nightly SQLite backup (db_backup task) keeps this many days of history under
# db/backups/ before pruning older files. db/ is already gitignored in the
# corpus repo, so backups never risk landing in MD corpus git history.
DB_BACKUP_RETENTION_DAYS: int = int(_get("DB_BACKUP_RETENTION_DAYS", 14))

# ---------------------------------------------------------------------------
# Offsite replication (offsite.py)
# ---------------------------------------------------------------------------
# "auto" (default) = push the corpus only if the configured remote exists, and
# stay quiet otherwise, so adding the remote on the box is the only step
# needed to switch this on. "1" = a missing remote is a hard failure that
# alerts. "0" = off entirely.
OFFSITE_PUSH_ENABLED: str  = str(_get("OFFSITE_PUSH_ENABLED", "auto"))
CORPUS_GIT_REMOTE: str     = _get("CORPUS_GIT_REMOTE", "origin")
CORPUS_GIT_BRANCH: str     = _get("CORPUS_GIT_BRANCH", "")   # blank = repo's current branch
OFFSITE_SSH_KEY_PATH: str  = _get("OFFSITE_SSH_KEY_PATH", "")
# Destination for gzipped SQLite snapshots. Only genuinely offsite if it is a
# mounted remote (rclone/S3/NFS); blank disables the snapshot half entirely.
OFFSITE_SNAPSHOT_DIR: str  = _get("OFFSITE_SNAPSHOT_DIR", "")

# Current user's nick for owner-filtering in recur files (must match owners: values in frontmatter)
USER_NICK: str = _get("USER_NICK", "ADMIN")

# ---------------------------------------------------------------------------
# Per-user path helper
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CurrentUser:
    """
    Frozen dataclass holding the authenticated user's identity and derived data paths.
    Instantiated in auth_utils.py after token validation; passed around as g.user_obj
    once Phase 6 wires it in.
    """
    username: str
    email: str = ""

    @property
    def data_root(self) -> Path:
        return DATA_ROOT / (self.username or DEV_USER)

    @property
    def md_root(self) -> Path:
        return self.data_root / "md"

    @property
    def db_path(self) -> Path:
        return self.data_root / "db" / "sqlite" / "pma.db"

    @property
    def v_db_path(self) -> Path:
        return self.data_root / "db" / "chroma"
