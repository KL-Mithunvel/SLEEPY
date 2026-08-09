"""
Tests for worker.py's task_queue interaction. main()'s infinite drain loop
itself isn't unit-tested (would never return) — this covers _enqueue_scheduled,
the function main() now also calls once at startup so `materialise` always
runs when the worker comes up, not only at the 00:05 IST cron window (which
the process may simply not be alive for). See docs/CORPUS_SCHEMA.md.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

import worker  # noqa: E402


@pytest.fixture()
def conn(app):
    import local_db
    return local_db.get_db()


def test_enqueue_scheduled_inserts_pending_task(conn):
    worker._enqueue_scheduled("materialise", {})

    row = conn.execute(
        "SELECT task_type, status, payload FROM task_queue ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["task_type"] == "materialise"
    assert row["status"] == "pending"
