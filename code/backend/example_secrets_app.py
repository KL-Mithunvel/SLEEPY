# Copy this file to secrets_app.py and fill in real values.
# secrets_app.py is gitignored — never commit it.
# In Docker, mount as read-only: /app/backend/secrets_app.py:ro

# ---------------------------------------------------------------------------
# AI (primary)
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = "sk-ant-..."     # preferred key name going forward
# CLAUDE_API_KEY = "sk-ant-..."      # legacy alias — also accepted

# ---------------------------------------------------------------------------
# In-app auth (leave blank for local dev with DEV_AUTH_BYPASS=1; required in
# prod — config.py refuses to start with APP_ENV=production and this blank)
# ---------------------------------------------------------------------------
AUTH_SECRET_KEY = ""      # generate via: python -c "import secrets; print(secrets.token_hex(32))"
AUTH_TOKEN_TTL_DAYS = 7   # how long a login stays valid before re-authenticating

# ---------------------------------------------------------------------------
# SQLite DB path (absolute, or relative to code/backend/)
# ---------------------------------------------------------------------------
SQLITE_DB_PATH = "../../data/klm/db/sqlite/pma.db"

# ---------------------------------------------------------------------------
# Office 365 email (MSAL client-credentials + Graph API sendMail)
# ---------------------------------------------------------------------------
O365_TENANT_ID     = ""              # Azure AD tenant ID
O365_CLIENT_ID     = ""              # Azure app registration client ID
O365_CLIENT_SECRET = ""              # Azure app registration client secret
O365_MAILBOX       = ""              # Mailbox the bot sends from, e.g. pmabot@smtw.in
O365_SENDER_NAME   = "PMA Bot"       # Display name in From: field
USER_EMAIL         = ""              # Where scheduled digests (briefing + deadlines) are sent

# ---------------------------------------------------------------------------
# Operational alerting (alerts.py)
# ---------------------------------------------------------------------------
ALERTS_ENABLED       = "1"           # "0" = record alerts in SQLite but never email
ALERT_EMAIL          = ""            # Defaults to USER_EMAIL when left blank
ALERT_COOLDOWN_HOURS = 6             # Per-alert-key throttle; stops a broken job mail-flooding

# ---------------------------------------------------------------------------
# Worker / indexing
# ---------------------------------------------------------------------------
INDEX_SYNC_INTERVAL_SEC = 300        # How often (s) to sync ChromaDB vs MD files

# ---------------------------------------------------------------------------
# News Watch
# ---------------------------------------------------------------------------
PMA_NEWS_RUN_ALL = "1"               # "0" = rotate 1/7 of projects/topics per night instead of all
