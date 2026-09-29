"""
Boot the real SLEEPY Flask app in production posture against the sandbox corpus.

    uv run python tooling/sandbox/run_sandbox.py           # keep existing sandbox
    uv run python tooling/sandbox/run_sandbox.py --reset   # wipe and reseed first

Binds 127.0.0.1:5055 only. No worker is started, so queued tasks (email,
reindex, ...) are recorded but never executed — attack scripts inspect the
task_queue table to see what an attacker managed to enqueue.
"""

import sys

import sandbox_env as sb

sb.apply_env()
sb.seed(reset="--reset" in sys.argv)
sb.create_users()

from app import app  # noqa: E402  (must follow apply_env)

if __name__ == "__main__":
    print(f"SLEEPY sandbox on {sb.BASE_URL}  (data: {sb.USER_ROOT})")
    print("accounts:", ", ".join(f"{u}/{p} [{r}]" for u, (p, r) in sb.USERS.items()))
    app.run(host=sb.HOST, port=sb.PORT, debug=False, use_reloader=False, threaded=True)
