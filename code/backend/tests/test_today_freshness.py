"""
Tests for the morning-briefing freshness/regeneration behavior of
GET /api/today, and the mandatory deterministic date/day header
ai_client.generate_morning_briefing() prepends to every briefing.
"""

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


@pytest.fixture()
def db():
    import local_db
    conn = local_db.get_db()
    yield conn
    conn.execute("DELETE FROM ai_events WHERE event_type = 'morning_briefing'")
    conn.commit()
    conn.close()


def _insert_briefing(db, text, created_at):
    db.execute(
        "INSERT INTO ai_events (event_type, result, accepted, created_at) "
        "VALUES ('morning_briefing', ?, 1, ?)",
        (text, created_at),
    )
    db.commit()


# ---------------------------------------------------------------------------
# _briefing_header — mandatory date/day format
# ---------------------------------------------------------------------------

def test_briefing_header_contains_day_name_and_date():
    import ai_client
    now = datetime(2026, 9, 16, 6, 30)  # a Wednesday
    header = ai_client._briefing_header(now)
    assert "Wednesday" in header
    assert "16-09-2026" in header
    assert "06:30" in header
    assert header.startswith("# Morning Briefing")


def test_generate_morning_briefing_prepends_header(monkeypatch, db):
    # Uses the `db` fixture (not a bare local_db.get_db()) so the ai_events
    # row this writes for real gets cleaned up — otherwise it leaks into the
    # shared in-memory test DB and pollutes the freshness-gate tests below,
    # which key off "most recent morning_briefing row".
    import ai_client
    monkeypatch.setattr(ai_client, "_build_project_task_summary", lambda root: "")
    monkeypatch.setattr(ai_client, "_load_inbox", lambda root: "")
    monkeypatch.setattr(
        ai_client, "chat",
        lambda messages, **kw: {
            "content": "## Focus Plan\n- Ship the thing",
            "model": "test-model", "latency_ms": 1, "input_tokens": 1, "output_tokens": 1,
        },
    )
    result = ai_client.generate_morning_briefing(db)
    assert result.startswith("# Morning Briefing —")
    assert "## Focus Plan" in result


# ---------------------------------------------------------------------------
# GET /api/today — regenerate-when-stale gate
# ---------------------------------------------------------------------------

def test_today_uses_stored_briefing_from_today_without_regenerating(client, monkeypatch, db):
    import task_scan
    monkeypatch.setattr(task_scan, "scan_todays_tasks", lambda data_root: [])

    calls = []
    import ai_client
    monkeypatch.setattr(ai_client, "generate_morning_briefing", lambda conn: calls.append(1) or "should not be used")

    today_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _insert_briefing(db, "# Morning Briefing — already fresh today", today_str)

    resp = client.get("/api/today")
    assert resp.status_code == 200
    assert resp.get_json()["briefing"] == "# Morning Briefing — already fresh today"
    assert calls == []  # generate_morning_briefing was never called


def test_today_regenerates_when_stored_briefing_is_from_a_previous_day(client, monkeypatch, db):
    import task_scan
    monkeypatch.setattr(task_scan, "scan_todays_tasks", lambda data_root: [])

    import ai_client
    monkeypatch.setattr(ai_client, "generate_morning_briefing", lambda conn: "# Morning Briefing — regenerated just now")

    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    _insert_briefing(db, "# Morning Briefing — stale from yesterday", yesterday_str)

    resp = client.get("/api/today")
    assert resp.status_code == 200
    assert resp.get_json()["briefing"] == "# Morning Briefing — regenerated just now"


def test_today_falls_back_to_stale_briefing_if_regeneration_fails(client, monkeypatch, db):
    """A same-day page load must never go from 'stale briefing' to 'no
    briefing at all' just because the on-demand LLM call happened to fail."""
    import task_scan
    monkeypatch.setattr(task_scan, "scan_todays_tasks", lambda data_root: [])

    import ai_client
    def _boom(conn):
        raise RuntimeError("llm down")
    monkeypatch.setattr(ai_client, "generate_morning_briefing", _boom)

    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    _insert_briefing(db, "# Morning Briefing — stale but better than nothing", yesterday_str)

    resp = client.get("/api/today")
    assert resp.status_code == 200
    assert resp.get_json()["briefing"] == "# Morning Briefing — stale but better than nothing"
