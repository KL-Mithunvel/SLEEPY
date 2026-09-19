"""
Tests for alerts.py — the task-failure alerting path that replaced "failed
jobs die silently in SQLite" (docs/SECURITY_REVIEW.md Layer 6).

Covers: recording, cooldown throttling per alert_key, the email-task loop
guard, missing-config degradation, and the worker hook that only fires on
terminal failure (not on a retry still in flight).
"""

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")


@pytest.fixture()
def conn(app):
    """
    Fresh connection per test, explicitly closed afterwards.

    These tests deliberately leave uncommitted writes behind (notify() must not
    commit), and on the shared-cache :memory: DB an open write transaction
    locks the table for every other connection — including the next test's.
    Rolling back and closing on teardown is what keeps them independent.
    """
    import local_db
    c = local_db.get_db()
    c.execute("DELETE FROM system_alerts")
    c.execute("DELETE FROM task_queue")
    c.commit()
    yield c
    c.rollback()
    local_db.return_db(c)


@pytest.fixture(autouse=True)
def alert_config(monkeypatch):
    import config
    monkeypatch.setattr(config, "ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "ALERT_EMAIL", "owner@example.invalid")
    monkeypatch.setattr(config, "ALERT_COOLDOWN_HOURS", 6)


def _emails(conn):
    return conn.execute(
        "SELECT * FROM task_queue WHERE task_type = 'email' ORDER BY id"
    ).fetchall()


def _alert_rows(conn):
    return conn.execute("SELECT * FROM system_alerts ORDER BY id").fetchall()


# ---------------------------------------------------------------------------
# notify()
# ---------------------------------------------------------------------------

def test_notify_records_and_enqueues_email(conn):
    import alerts

    assert alerts.notify(conn, "k1", "subject here", "body here") is True

    rows = _alert_rows(conn)
    assert len(rows) == 1
    assert rows[0]["alert_key"] == "k1"
    assert rows[0]["suppressed"] == 0
    assert rows[0]["emailed"] == 1

    mails = _emails(conn)
    assert len(mails) == 1
    assert "owner@example.invalid" in mails[0]["payload"]
    assert "subject here" in mails[0]["payload"]


def test_notify_does_not_commit(conn):
    """Handlers must not commit — notify() must leave that to its caller."""
    import alerts

    alerts.notify(conn, "k-nocommit", "s", "b")
    conn.rollback()

    assert _alert_rows(conn) == []
    assert _emails(conn) == []


def test_notify_throttles_repeat_within_cooldown(conn):
    import alerts

    assert alerts.notify(conn, "same-key", "first", "b") is True
    assert alerts.notify(conn, "same-key", "second", "b") is False
    assert alerts.notify(conn, "same-key", "third", "b") is False

    rows = _alert_rows(conn)
    assert [r["suppressed"] for r in rows] == [0, 1, 1]   # all three recorded
    assert len(_emails(conn)) == 1                        # only one mail


def test_notify_emails_again_after_cooldown_expires(conn):
    import alerts

    assert alerts.notify(conn, "aged", "first", "b") is True
    old = (datetime.now() - timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE system_alerts SET created_at = ? WHERE alert_key = 'aged'", (old,))

    assert alerts.notify(conn, "aged", "second", "b") is True
    assert len(_emails(conn)) == 2


def test_notify_throttles_per_key_not_globally(conn):
    import alerts

    assert alerts.notify(conn, "key-a", "a", "b") is True
    assert alerts.notify(conn, "key-b", "b", "b") is True
    assert len(_emails(conn)) == 2


def test_notify_disabled_records_but_does_not_email(conn, monkeypatch):
    import alerts
    import config
    monkeypatch.setattr(config, "ALERTS_ENABLED", False)

    assert alerts.notify(conn, "k", "s", "b") is False
    assert len(_alert_rows(conn)) == 1
    assert _emails(conn) == []


def test_notify_without_destination_records_but_does_not_email(conn, monkeypatch):
    import alerts
    import config
    monkeypatch.setattr(config, "ALERT_EMAIL", "")

    assert alerts.notify(conn, "k", "s", "b") is False
    rows = _alert_rows(conn)
    assert len(rows) == 1
    assert rows[0]["emailed"] == 0
    assert _emails(conn) == []


def test_notify_never_raises_on_internal_error(conn, monkeypatch):
    """An alert blowing up must not take down whatever was reporting a problem."""
    import alerts
    import task_queue

    def _boom(*a, **kw):
        raise RuntimeError("queue exploded")

    monkeypatch.setattr(task_queue, "enqueue", _boom)
    assert alerts.notify(conn, "k", "s", "b") is False


# ---------------------------------------------------------------------------
# task_failed()
# ---------------------------------------------------------------------------

def test_task_failed_builds_alert_with_context(conn):
    import alerts

    task = {"id": 42, "task_type": "materialise", "attempts": 3,
            "max_attempts": 3, "created_at": "2026-09-19 00:05:00",
            "last_error": "boom in materialiser"}
    assert alerts.task_failed(conn, task) is True

    row = _alert_rows(conn)[0]
    assert row["alert_key"] == "task_failed:materialise"
    assert "materialise" in row["subject"]
    assert "boom in materialiser" in row["body"]
    assert "3/3" in row["body"]


def test_task_failed_never_alerts_on_email_task(conn):
    """The alert is delivered by an email task — alerting on one would loop."""
    import alerts

    task = {"id": 7, "task_type": "email", "attempts": 3, "max_attempts": 3,
            "created_at": "x", "last_error": "graph api down"}
    assert alerts.task_failed(conn, task) is False
    assert _alert_rows(conn) == []
    assert _emails(conn) == []


# ---------------------------------------------------------------------------
# worker hook — only terminal failures alert
# ---------------------------------------------------------------------------

def test_worker_alerts_once_task_is_permanently_failed(conn):
    import task_queue
    import worker

    tid = task_queue.enqueue(conn, "definitely-not-a-real-task-type", {})
    conn.execute("UPDATE task_queue SET max_attempts = 1 WHERE id = ?", (tid,))
    conn.commit()

    worker._drain_once()

    row = task_queue.get(conn, tid)
    assert row["status"] == "failed"

    keys = [r["alert_key"] for r in _alert_rows(conn)]
    assert "task_failed:definitely-not-a-real-task-type" in keys
    assert len(_emails(conn)) == 1


def test_worker_does_not_alert_while_retries_remain(conn):
    import task_queue
    import worker

    tid = task_queue.enqueue(conn, "also-not-a-real-task-type", {})
    conn.execute("UPDATE task_queue SET max_attempts = 3 WHERE id = ?", (tid,))
    conn.commit()

    worker._drain_once()

    row = task_queue.get(conn, tid)
    assert row["status"] == "pending"      # backed off for a retry
    assert _alert_rows(conn) == []         # not news yet
    assert _emails(conn) == []
