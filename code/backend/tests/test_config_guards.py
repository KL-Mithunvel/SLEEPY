"""
S4/S6 security regression tests: config.py must hard-fail at import time for
dangerous prod misconfigurations. These raise at module import, so each case
is run in a fresh subprocess (reloading an already-imported config module in
the shared test process would pollute every other test's config state).
"""

import os
import subprocess
import sys

_BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..")


def _run_import(env_overrides: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import config"],
        cwd=_BACKEND_DIR, env=env, capture_output=True, text=True, timeout=30,
    )


def test_prod_with_dev_auth_bypass_refuses_to_start():
    result = _run_import({
        "APP_ENV": "production", "DEV_AUTH_BYPASS": "1",
        "ANTHROPIC_API_KEY": "sk-ant-fake", "SQLITE_DB_PATH": ":memory:",
    })
    assert result.returncode != 0
    assert "DEV_AUTH_BYPASS" in result.stderr


def test_prod_without_api_key_refuses_to_start():
    result = _run_import({
        "APP_ENV": "production", "DEV_AUTH_BYPASS": "0",
        "ANTHROPIC_API_KEY": "", "CLAUDE_API_KEY": "", "SQLITE_DB_PATH": ":memory:",
    })
    assert result.returncode != 0
    assert "ANTHROPIC_API_KEY" in result.stderr


def test_prod_with_bypass_off_and_api_key_starts_cleanly():
    result = _run_import({
        "APP_ENV": "production", "DEV_AUTH_BYPASS": "0",
        "ANTHROPIC_API_KEY": "sk-ant-fake", "SQLITE_DB_PATH": ":memory:",
    })
    assert result.returncode == 0, result.stderr


def test_dev_env_with_bypass_and_no_api_key_starts_cleanly():
    """The exact combination this repo's own dev setup uses today must keep working."""
    result = _run_import({
        "APP_ENV": "development", "DEV_AUTH_BYPASS": "1",
        "ANTHROPIC_API_KEY": "", "CLAUDE_API_KEY": "", "SQLITE_DB_PATH": ":memory:",
    })
    assert result.returncode == 0, result.stderr
