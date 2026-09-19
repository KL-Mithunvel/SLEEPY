"""
Dev entry point — starts the Vite frontend dev server and the background
worker (APScheduler + task_queue drainer) alongside Flask so
`uv run python main.py` brings up the whole stack for local testing.
Without the worker running, nightly jobs (materialise, morning_briefing,
housekeeping, news_watch, etc.) never fire and the corpus silently goes stale.

Production (Proxmox) uses gunicorn against app:app directly, with the worker
as its own container (see docker-compose.yml) — this script is dev only.
"""

import atexit
import os
import subprocess
import sys
import threading
import time

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
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "code", "backend"))
WORKER_SCRIPT = os.path.join(BACKEND_DIR, "worker.py")

# How long the worker gets to finish its current task and exit on its own
# before it is terminated. Long enough for a normal task, short enough that
# stopping the dev stack still feels instant.
WORKER_GRACE_SEC = 15

_frontend_proc: subprocess.Popen | None = None
_worker_proc: subprocess.Popen | None = None


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
    if not (_frontend_proc and _frontend_proc.poll() is None):
        return

    print("[main] Stopping frontend...")
    if os.name == "nt":
        # vite.cmd spawns node.exe as a child, and terminate() kills only the
        # .cmd shim — the orphaned node keeps holding port 5173 and makes the
        # stop script wait out its entire grace period before forcing it. The
        # dev server holds no state worth unwinding, so take the whole tree.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(_frontend_proc.pid)],
            capture_output=True,
        )
    else:
        _frontend_proc.terminate()

    try:
        _frontend_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _frontend_proc.kill()


def _start_worker() -> subprocess.Popen:
    # Runs as its own OS process (never imported into the Flask process) —
    # same interpreter as this one, cwd set so its bare imports resolve.
    print(f"[main] Starting worker: {WORKER_SCRIPT}")
    return subprocess.Popen([sys.executable, WORKER_SCRIPT], cwd=BACKEND_DIR)


def _stop_worker():
    """
    Stop the worker, giving it a chance to finish cleanly first.

    Popen.terminate() on Windows is a hard TerminateProcess — no signal
    handler runs, so a task in flight was abandoned and its queue row sat
    'running' for the full 30-minute lock window. The worker now also watches
    the stop sentinel, so the sentinel is tried first and terminate() is only
    the fallback for a worker that has stopped responding.
    """
    if not (_worker_proc and _worker_proc.poll() is None):
        return

    print("[main] Stopping worker...")
    created_sentinel = _write_stop_sentinel()
    try:
        _worker_proc.wait(timeout=WORKER_GRACE_SEC)
        print("[main] Worker stopped cleanly.")
        return
    except subprocess.TimeoutExpired:
        print(f"[main] Worker did not stop within {WORKER_GRACE_SEC}s — terminating.")
    finally:
        if created_sentinel:
            _clear_stop_sentinel()

    _worker_proc.terminate()
    try:
        _worker_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _worker_proc.kill()


def _write_stop_sentinel() -> bool:
    """Create the stop sentinel. Returns False if it was already there (the
    stop script made it), so we don't delete someone else's signal."""
    try:
        if os.path.exists(config.STOP_SENTINEL_PATH):
            return False
        with open(config.STOP_SENTINEL_PATH, "w") as f:
            f.write(str(os.getpid()))
        return True
    except OSError as exc:
        print(f"[main] WARNING: could not write stop sentinel: {exc}")
        return False


def _clear_stop_sentinel():
    try:
        os.remove(config.STOP_SENTINEL_PATH)
    except OSError:
        pass


def _watch_for_stop():
    """
    Poll for the stop sentinel dropped by tooling/stop-sleepy.ps1.

    Windows offers no way for that script to ask this console process to shut
    down politely — taskkill /F is a hard kill, which is exactly what used to
    leave .git/index.lock behind and wedge every later corpus write. Watching
    for a file gives the stop script a way to say "please stop" that runs the
    same teardown as Ctrl-C.
    """
    while True:
        time.sleep(1)
        if not os.path.exists(config.STOP_SENTINEL_PATH):
            continue
        print("[main] Stop sentinel seen — shutting down.")
        _clear_stop_sentinel()
        _stop_frontend()
        _stop_worker()
        # Children are down and nothing else holds state worth unwinding; the
        # dev server has no clean programmatic shutdown, so exit outright.
        os._exit(0)


if __name__ == "__main__":
    local_db.init_db()

    # Clear anything a previous hard kill left behind before either process
    # tries to touch the corpus repo (see md_editor.clear_stale_git_locks).
    _clear_stop_sentinel()
    try:
        import md_editor
        for path in md_editor.clear_stale_git_locks(config.USER_DATA_ROOT):
            print(f"[main] Cleared abandoned lock: {path}")
    except Exception as exc:
        print(f"[main] WARNING: startup lock sweep failed: {exc}")

    _frontend_proc = _start_frontend()
    atexit.register(_stop_frontend)

    _worker_proc = _start_worker()
    atexit.register(_stop_worker)

    threading.Thread(target=_watch_for_stop, daemon=True).start()

    print("[main] Backend:  http://localhost:5000")
    print("[main] Frontend: http://localhost:5173")
    print("[main] Worker:   running (materialise/briefing/housekeeping/news_watch cron + queue drain)")

    try:
        # use_reloader=False — Werkzeug's reloader re-execs this script,
        # which would spawn duplicate frontend/worker processes.
        # use_debugger=False — DEBUG=1 keeps verbose logging/tracebacks, but the
        # interactive Werkzeug debugger is a remote code-execution console; with
        # DEV_AUTH_BYPASS=1 also on, nothing should offer that on any port.
        app.run(debug=config.DEBUG, port=5000, use_reloader=False, use_debugger=False)
    finally:
        _stop_frontend()
        _stop_worker()
