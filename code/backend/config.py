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
# Accept either key name; ANTHROPIC_API_KEY is preferred going forward.
ANTHROPIC_API_KEY: str = _get("ANTHROPIC_API_KEY") or _get("CLAUDE_API_KEY", "")
CLAUDE_API_KEY: str = ANTHROPIC_API_KEY  # backward-compat alias

LLM_DEFAULT_MODEL: str    = _get("LLM_DEFAULT_MODEL", "claude-sonnet-4-6")
LLM_MAX_CONTEXT_CHUNKS: int = int(_get("LLM_MAX_CONTEXT_CHUNKS", 8))
LLM_MAX_TOKENS: int         = int(_get("LLM_MAX_TOKENS", 4096))

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
DEV_AUTH_BYPASS: bool = str(_get("DEV_AUTH_BYPASS", "0")).strip() in ("1", "true", "yes")
DEV_USER: str         = _get("DEV_USER", "kla")

KEYCLOAK_PUBLIC_URL: str = _get("KEYCLOAK_PUBLIC_URL", "")
KEYCLOAK_REALM: str      = _get("KEYCLOAK_REALM", "")
KEYCLOAK_CLIENT_ID: str  = _get("KEYCLOAK_CLIENT_ID", "pma")
KEYCLOAK_HOST_IP: str    = _get("KEYCLOAK_HOST_IP", "")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
SQLITE_DB_PATH: str = _get("SQLITE_DB_PATH", "../../data/kla/db/sqlite/pma.db")

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
USER_DATA_ROOT: str = str(_get("USER_DATA_ROOT", str(_REPO_ROOT / "data" / "kla")))

# ---------------------------------------------------------------------------
# ChromaDB
# ---------------------------------------------------------------------------
CHROMA_HOST: str       = _get("CHROMA_HOST", "")
CHROMA_PORT: int       = int(_get("CHROMA_PORT", 8001))
CHROMA_PATH: str       = str(_get("CHROMA_PATH", str(_REPO_ROOT / "data" / "kla" / "db" / "chroma")))
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

# ---------------------------------------------------------------------------
# Integrations — Telegram
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str      = _get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_DEFAULT_CHAT_ID: str = _get("TELEGRAM_DEFAULT_CHAT_ID", "")

# ---------------------------------------------------------------------------
# Integrations — Jira Cloud
# ---------------------------------------------------------------------------
JIRA_BASE_URL: str    = _get("JIRA_BASE_URL", "")
JIRA_USER_EMAIL: str  = _get("JIRA_USER_EMAIL", "")
JIRA_API_TOKEN: str   = _get("JIRA_API_TOKEN", "")

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
MCP_API_KEY: str           = _get("MCP_API_KEY", "")
MCP_USER: str              = _get("MCP_USER", "admin")
MCP_OAUTH_CLIENT_ID: str   = _get("MCP_OAUTH_CLIENT_ID", "pma-mcp")
MCP_OAUTH_CLIENT_SECRET: str = _get("MCP_OAUTH_CLIENT_SECRET", "")

# ---------------------------------------------------------------------------
# Worker / indexing
# ---------------------------------------------------------------------------
INDEX_SYNC_INTERVAL_SEC: int = int(_get("INDEX_SYNC_INTERVAL_SEC", 300))

# When "1", skip registering the midnight news-watch cron (manual triggers still work)
NEWS_WATCH_CRON_DISABLED: bool = str(_get("PMA_NEWS_WATCH_CRON_DISABLED", "0")).strip() in ("1", "true", "yes")

# When "1", news watch runs all active projects every day (ignores day-of-week rotation)
NEWS_RUN_ALL: bool = str(_get("PMA_NEWS_RUN_ALL", "0")).strip() in ("1", "true", "yes")

# Projects edited within this many days count as "recently active" (→ 5 items/topic)
RECENCY_DAYS: int = int(_get("RECENCY_DAYS", 14))

# Anthropic SDK retry count for batch-create and LLM-dedup calls
NEWS_MAX_RETRIES: int = int(_get("NEWS_MAX_RETRIES", 8))

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
        return self.data_root / "db" / "sqlite" / "app.db"

    @property
    def v_db_path(self) -> Path:
        return self.data_root / "db" / "chroma"
