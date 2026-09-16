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


# ---------------------------------------------------------------------------
# move_file / delete_file — staged, confirm-gated corpus file ops
# ---------------------------------------------------------------------------

@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    import config
    root = str(tmp_path / "data")
    os.makedirs(root)
    monkeypatch.setattr(config, "USER_DATA_ROOT", root)
    return root


def _get_tool(conn, name):
    import tools_registry
    tools = tools_registry.build_tools(conn)
    return next(t for t in tools if t.name == name)


def test_move_file_tool_stages_pending_move(conn, data_root):
    src = os.path.join(data_root, "SMTW", "a.md")
    os.makedirs(os.path.dirname(src))
    with open(src, "w") as f:
        f.write("# A")

    tool = _get_tool(conn, "move_file")
    result = tool.handler({"src_path": "SMTW/a.md", "dst_path": "VIT/a.md"})
    assert "staged for user review" in result.lower()


def test_move_file_tool_reports_error_for_missing_source(conn, data_root):
    tool = _get_tool(conn, "move_file")
    result = tool.handler({"src_path": "SMTW/ghost.md", "dst_path": "VIT/ghost.md"})
    assert result.startswith("[error:")


def test_delete_file_tool_stages_pending_delete(conn, data_root):
    path = os.path.join(data_root, "SMTW", "dup.md")
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as f:
        f.write("# Dup")

    tool = _get_tool(conn, "delete_file")
    result = tool.handler({"path": "SMTW/dup.md"})
    assert "staged for user review" in result.lower()


def test_delete_file_tool_reports_error_for_missing_file(conn, data_root):
    tool = _get_tool(conn, "delete_file")
    result = tool.handler({"path": "SMTW/ghost.md"})
    assert result.startswith("[error:")
