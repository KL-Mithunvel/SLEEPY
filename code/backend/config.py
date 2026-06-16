import os
import secrets_app

# --- AI ---
CLAUDE_API_KEY = secrets_app.CLAUDE_API_KEY

# --- Auth ---
DEV_AUTH_BYPASS = os.getenv("DEV_AUTH_BYPASS", "0") == "1"
KEYCLOAK_PUBLIC_URL = secrets_app.KEYCLOAK_PUBLIC_URL
KEYCLOAK_REALM = secrets_app.KEYCLOAK_REALM
KEYCLOAK_CLIENT_ID = secrets_app.KEYCLOAK_CLIENT_ID
KEYCLOAK_HOST_IP = secrets_app.KEYCLOAK_HOST_IP  # empty = use public URL for JWKS

# --- Database ---
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", secrets_app.SQLITE_DB_PATH)

# --- Flask ---
DEBUG = os.getenv("DEBUG", "0") == "1"
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")

# --- Performance ---
SLOW_REQUEST_MS = int(os.getenv("SLOW_REQUEST_MS", "3000"))

# --- Data ---
# Absolute path to the user's MD corpus root (the folder containing ABOUT.md, OU folders, etc.)
USER_DATA_ROOT = os.getenv("USER_DATA_ROOT", os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../data/kla")
))
