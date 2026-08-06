"""
Unit tests for code/backend/integrations.py — chunking/formatting helpers and
the config-guard behavior of send_telegram/list_recent_mail (no real network
calls are made; HTTP is monkeypatched where needed).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

import integrations


def test_chunk_message_under_limit_is_single_chunk():
    assert integrations._chunk_message("short message") == ["short message"]


def test_chunk_message_splits_many_lines():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(200))
    chunks = integrations._chunk_message(text)
    assert len(chunks) > 1
    assert all(len(c) <= integrations._TELEGRAM_MAX_LEN for c in chunks)
    assert "\n".join(chunks) == text


def test_chunk_message_hard_splits_a_single_oversized_line():
    # A line with no newlines at all still must not exceed the cap.
    text = "x" * 5000
    chunks = integrations._chunk_message(text)
    assert all(len(c) <= integrations._TELEGRAM_MAX_LEN for c in chunks)
    assert "".join(chunks) == text


def test_telegramize_converts_headings_to_bold():
    result = integrations._telegramize("## Heading\nbody text\n### Sub")
    assert "## " not in result
    assert "*Heading*" in result
    assert "*Sub*" in result
    assert "body text" in result


def test_send_telegram_not_configured_returns_false(monkeypatch):
    import config
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_DEFAULT_CHAT_ID", "")
    assert integrations.send_telegram("hello") is False


def test_send_telegram_chunks_and_sends_sequentially(monkeypatch):
    import config
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(config, "TELEGRAM_DEFAULT_CHAT_ID", "123")

    calls = []

    def _fake_http_json(url, *, method="GET", headers=None, body=None, timeout=15):
        calls.append(body)
        return {"ok": True}

    monkeypatch.setattr(integrations, "_http_json", _fake_http_json)

    long_message = "\n".join(f"line {i}" * 30 for i in range(300))
    assert integrations.send_telegram(long_message) is True
    assert len(calls) > 1
    assert all(len(c["text"]) <= integrations._TELEGRAM_MAX_LEN for c in calls)


def test_list_recent_mail_not_configured_returns_empty(monkeypatch):
    import config
    monkeypatch.setattr(config, "O365_CLIENT_ID", "")
    monkeypatch.setattr(config, "O365_CLIENT_SECRET", "")
    monkeypatch.setattr(config, "O365_TENANT_ID", "")
    monkeypatch.setattr(config, "O365_MAILBOX", "")
    assert integrations.list_recent_mail() == []
