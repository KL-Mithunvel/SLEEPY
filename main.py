"""
Dev entry point — starts the Vite frontend dev server alongside Flask so
`uv run python main.py` brings up the whole stack for local testing.

Production (Proxmox) uses gunicorn against app:app directly; the frontend
is built and served as static files. This script is for local dev only.
"""

import atexit
import os
import subprocess
import sys

# Default to dev auth bypass for this local-only runner. Respect an
# explicitly-set env var (e.g. DEV_AUTH_BYPASS=0 to test the Keycloak path).
os.environ.setdefault("DEV_AUTH_BYPASS", "1")

# Backend modules use bare imports (import config, etc.) and live in
# code/backend rather than next to this entry point — put them on the path.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "code", "backend"))

import config
import local_db
from app import app

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "code", "frontend"))

_frontend_proc: subprocess.Popen | None = None


def _vite_binary() -> str:
    name = "vite.cmd" if os.name == "nt" else "vite"
    path = os.path.join(FRONTEND_DIR, "node_modules", ".bin", name)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Vite binary not found at {path}. Run 'npm install' in {FRONTEND_DIR} first."
        )
    return path


def _start_frontend() -> subprocess.Popen | None:
    try:
        vite_bin = _vite_binary()
    except FileNotFoundError as exc:
        print(f"[main] WARNING: {exc}")
        print("[main] Continuing with backend only.")
        return None

    print(f"[main] Starting frontend: {vite_bin}")
    return subprocess.Popen([vite_bin], cwd=FRONTEND_DIR)


def _stop_frontend():
    if _frontend_proc and _frontend_proc.poll() is None:
        print("[main] Stopping frontend...")
        _frontend_proc.terminate()
        try:
            _frontend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _frontend_proc.kill()


if __name__ == "__main__":
    local_db.init_db()

    _frontend_proc = _start_frontend()
    atexit.register(_stop_frontend)

    print("[main] Backend:  http://localhost:5000")
    print("[main] Frontend: http://localhost:5173")

    try:
        # use_reloader=False — Werkzeug's reloader re-execs this script,
        # which would spawn a second frontend process.
        app.run(debug=config.DEBUG, port=5000, use_reloader=False)
    finally:
        _stop_frontend()
