# Copy this file to secrets_app.py and fill in real values.
# secrets_app.py is gitignored — never commit it.

# Claude API key (primary AI provider)
CLAUDE_API_KEY = "sk-ant-..."

# Keycloak (wired in at deploy time; leave blank for local dev with DEV_AUTH_BYPASS=1)
KEYCLOAK_HOST_IP = ""          # LAN IP for internal JWKS fetch; empty = use public URL
KEYCLOAK_PUBLIC_URL = "https://auth.office.smtw.in"
KEYCLOAK_REALM = "Office.smtw.in"
KEYCLOAK_CLIENT_ID = "pma"

# SQLite DB path (absolute or relative to backend/)
SQLITE_DB_PATH = "../../data/kla/db/sqlite/pma.db"
