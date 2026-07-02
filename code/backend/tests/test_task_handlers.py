"""
Tests for task_handlers._handle_morning_briefing (goal-planning + email extension).
Network calls, LLM calls, and filesystem writes are monkeypatched.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")


@pytest.fixture()
def conn(app):
    """In-memory SQLite connection with schema applied."""
    import local_db
    return local_db.get_db()


def _patch_briefing(monkeypatch, text="Today's briefing."):
    import ai_client
    monkeypatch.setattr(ai_client, "generate_morning_briefing", lambda conn: text)


def _patch_goal_planning(monkeypatch, digest_lines=None, raise_error=False):
    import goal_planner
    if raise_error:
        def _boom(data_root, user_nick="ADMIN"):
            raise RuntimeError("goal planning failed")
        monkeypatch.setattr(goal_planner, "run_goal_planning", _boom)
    else:
        monkeypatch.setattr(
            goal_planner, "run_goal_planning",
            lambda data_root, user_nick="ADMIN": {"digest_lines": digest_lines or []},
        )


def test_morning_briefing_emails_combined_digest(monkeypatch, conn):
    import integrations
    import task_handlers

    _patch_briefing(monkeypatch, "Today's briefing text.")
    _patch_goal_planning(monkeypatch, digest_lines=["**ProjX** — ⏰ 3 days left — Today: write docs"])

    sent = []
    monkeypatch.setattr(
        integrations, "send_email",
        lambda to, subject, body, **kw: sent.append((to, subject, body)) or True,
    )

    task_handlers._handle_morning_briefing({}, conn)

    assert len(sent) == 1
    to, subject, body = sent[0]
    assert "Today's briefing text." in body
    assert "## ⏰ Project Deadlines" in body
    assert "ProjX" in body
    assert "Morning Briefing" in subject


def test_morning_briefing_omits_deadlines_section_when_empty(monkeypatch, conn):
    import integrations
    import task_handlers

    _patch_briefing(monkeypatch, "Today's briefing text.")
    _patch_goal_planning(monkeypatch, digest_lines=[])

    sent = []
    monkeypatch.setattr(
        integrations, "send_email",
        lambda to, subject, body, **kw: sent.append(body) or True,
    )

    task_handlers._handle_morning_briefing({}, conn)

    assert len(sent) == 1
    assert "## ⏰ Project Deadlines" not in sent[0]


def test_morning_briefing_email_failure_does_not_raise(monkeypatch, conn):
    import integrations
    import task_handlers

    _patch_briefing(monkeypatch)
    _patch_goal_planning(monkeypatch)

    def _boom(*a, **kw):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(integrations, "send_email", _boom)

    task_handlers._handle_morning_briefing({}, conn)  # should not raise


def test_morning_briefing_goal_planning_failure_does_not_raise(monkeypatch, conn):
    import integrations
    import task_handlers

    _patch_briefing(monkeypatch)
    _patch_goal_planning(monkeypatch, raise_error=True)

    sent = []
    monkeypatch.setattr(
        integrations, "send_email",
        lambda to, subject, body, **kw: sent.append(body) or True,
    )

    task_handlers._handle_morning_briefing({}, conn)  # should not raise

    assert len(sent) == 1
    assert "## ⏰ Project Deadlines" not in sent[0]
