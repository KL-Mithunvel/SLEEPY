"""
Tests for the LLM tool registry — focused on the send_email recipient
allowlist (S2 security fix: the chat LLM's context includes externally
controlled text via news_watch.py, so send_email is the one write action
reachable by prompt injection if left unrestricted).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")


def _get_send_email_tool(conn):
    import tools_registry
    tools = tools_registry.build_tools(conn)
    return next(t for t in tools if t.name == "send_email")


@pytest.fixture()
def conn(app):
    import local_db
    return local_db.get_db()


def test_is_allowed_email_recipient_owner_email(monkeypatch):
    import config
    import tools_registry
    monkeypatch.setattr(config, "USER_EMAIL", "klm@smtw.in")
    assert tools_registry._is_allowed_email_recipient("klm@smtw.in") is True
    assert tools_registry._is_allowed_email_recipient("KLM@SMTW.IN") is True


def test_is_allowed_email_recipient_company_domain(monkeypatch):
    import config
    import tools_registry
    monkeypatch.setattr(config, "USER_EMAIL", "klm@smtw.in")
    assert tools_registry._is_allowed_email_recipient("colleague@smtw.in") is True


def test_is_allowed_email_recipient_rejects_external(monkeypatch):
    import config
    import tools_registry
    monkeypatch.setattr(config, "USER_EMAIL", "klm@smtw.in")
    assert tools_registry._is_allowed_email_recipient("attacker@evil.com") is False


def test_is_allowed_email_recipient_rejects_empty():
    import tools_registry
    assert tools_registry._is_allowed_email_recipient("") is False
    assert tools_registry._is_allowed_email_recipient(None) is False


def test_send_email_tool_rejects_external_recipient(conn, monkeypatch):
    import config
    monkeypatch.setattr(config, "USER_EMAIL", "klm@smtw.in")

    tool = _get_send_email_tool(conn)
    result = tool.handler({"to": "attacker@evil.com", "subject": "Hi", "body": "Exfil attempt"})
    assert "not an allowed recipient" in result


def test_send_email_tool_accepts_owner_email(conn, monkeypatch):
    import config
    monkeypatch.setattr(config, "USER_EMAIL", "klm@smtw.in")

    tool = _get_send_email_tool(conn)
    result = tool.handler({"to": "klm@smtw.in", "subject": "Hi", "body": "Legit"})
    assert "queued" in result.lower()
