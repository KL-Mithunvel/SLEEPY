import logging
import time

from flask import Flask, g, jsonify, request
from flask_cors import CORS

import auth_utils
import config
import local_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=config.CORS_ORIGINS, supports_credentials=True)

# ---------------------------------------------------------------------------
# DB lifecycle
# ---------------------------------------------------------------------------

@app.teardown_appcontext
def _close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        local_db.return_db(conn)


def get_db():
    if "db" not in g:
        g.db = local_db.get_db()
    return g.db


# ---------------------------------------------------------------------------
# Slow-request logger — must run before auth so g._req_start is always set
# ---------------------------------------------------------------------------

@app.before_request
def _start_timer():
    g._req_start = time.monotonic()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.before_request
def _auth():
    return auth_utils.validate_token()


@app.after_request
def _log_slow(response):
    elapsed_ms = int((time.monotonic() - g._req_start) * 1000)
    if elapsed_ms >= config.SLOW_REQUEST_MS:
        logger.warning("SLOW %d ms  %s %s  status=%s",
                       elapsed_ms, request.method, request.path, response.status_code)
    return response


# ---------------------------------------------------------------------------
# Health check (no auth)
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Auth info endpoints
# ---------------------------------------------------------------------------

@app.get("/api/auth/config")
def auth_config():
    return jsonify({
        "url": config.KEYCLOAK_PUBLIC_URL,
        "realm": config.KEYCLOAK_REALM,
        "clientId": config.KEYCLOAK_CLIENT_ID,
        "devBypass": config.DEV_AUTH_BYPASS,
    })


@app.get("/api/auth/me")
def auth_me():
    user = g.user
    return jsonify({
        "sub": user["sub"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "permissions": list(user["permissions"]),
    })


# ---------------------------------------------------------------------------
# Blueprints
# ---------------------------------------------------------------------------
from ai_bp import ai_bp                # noqa: E402
from today_bp import today_bp          # noqa: E402
from projects_bp import projects_bp    # noqa: E402
from logs_bp import logs_bp            # noqa: E402

app.register_blueprint(ai_bp)
app.register_blueprint(today_bp)
app.register_blueprint(projects_bp)
app.register_blueprint(logs_bp)
