# Copy this file to secrets_app.py and fill in real values.
# secrets_app.py is gitignored — never commit it.
# In Docker, mount as read-only: /app/backend/secrets_app.py:ro

# ---------------------------------------------------------------------------
# AI (primary)
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = "sk-ant-..."     # preferred key name going forward
# CLAUDE_API_KEY = "sk-ant-..."      # legacy alias — also accepted

# ---------------------------------------------------------------------------
# Keycloak (leave blank for local dev with DEV_AUTH_BYPASS=1)
# ---------------------------------------------------------------------------
KEYCLOAK_HOST_IP    = ""             # LAN IP for direct JWKS fetch; empty = use public URL
KEYCLOAK_PUBLIC_URL = "https://auth.office.smtw.in"
KEYCLOAK_REALM      = "Office.smtw.in"
KEYCLOAK_CLIENT_ID  = "pma"

# ---------------------------------------------------------------------------
# SQLite DB path (absolute, or relative to code/backend/)
# ---------------------------------------------------------------------------
SQLITE_DB_PATH = "../../data/kla/db/sqlite/pma.db"

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
# MCP server (leave blank to disable)
# ---------------------------------------------------------------------------
MCP_API_KEY           = ""           # Static key for X-API-Key header auth
MCP_USER              = "admin"      # Username mapped to MCP_API_KEY
MCP_OAUTH_CLIENT_ID   = "pma-mcp"
MCP_OAUTH_CLIENT_SECRET = ""

# ---------------------------------------------------------------------------
# Worker / indexing
# ---------------------------------------------------------------------------
INDEX_SYNC_INTERVAL_SEC = 300        # How often (s) to sync ChromaDB vs MD files

# ---------------------------------------------------------------------------
# News Watch
# ---------------------------------------------------------------------------
PMA_NEWS_RUN_ALL = "0"               # "1" = run every day regardless of rotation (few-projects mode)
