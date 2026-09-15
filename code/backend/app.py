import logging
import time

from flask import Flask, g, jsonify, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

import auth_utils
import config
import local_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Idempotent (guarded by db_version) — main.py and worker.py both call this
# explicitly too, but gunicorn imports this module directly with neither of
# those wrappers, so app.py must initialize its own DB connection here or
# every DB-touching route 500s with "_db_path is None".
local_db.init_db()

app = Flask(__name__)
# nginx sits directly in front in prod (see tooling/nginx-klm.smtw.in.conf) and
# sets X-Forwarded-For/X-Forwarded-Proto — without this, every login_events row
# would log nginx's own address (127.0.0.1) instead of the real client IP.
# Trusting exactly one proxy hop.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
# Bearer-token auth only, no cookies — supports_credentials isn't needed and
# widens the CORS surface for no benefit.
CORS(app, origins=config.CORS_ORIGINS)
# Unbounded POST bodies would go straight into the LLM (cost-abuse risk if a
# token ever leaks) — 2 MB comfortably covers chat messages and MD file saves.
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

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
# Blueprints
# ---------------------------------------------------------------------------
from auth_bp import auth_bp                    # noqa: E402
from admin_bp import admin_bp                  # noqa: E402
from ai_bp import ai_bp                        # noqa: E402
from corpus_bp import corpus_bp                # noqa: E402
from integrations_bp import integrations_bp    # noqa: E402
from today_bp import today_bp                  # noqa: E402
from projects_bp import projects_bp            # noqa: E402
from logs_bp import logs_bp                    # noqa: E402

app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(ai_bp)
app.register_blueprint(corpus_bp)
app.register_blueprint(integrations_bp)
app.register_blueprint(today_bp)
app.register_blueprint(projects_bp)
app.register_blueprint(logs_bp)
