"""
Unit tests for code/backend/integrations.py — the O365 Graph API send_email
config-guard and success/failure paths (no real network calls are made;
HTTP and token acquisition are monkeypatched).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

import integrations


def test_send_email_not_configured_returns_false(monkeypatch):
    import config
    monkeypatch.setattr(config, "O365_CLIENT_ID", "")
    monkeypatch.setattr(config, "O365_CLIENT_SECRET", "")
    monkeypatch.setattr(config, "O365_TENANT_ID", "")
    monkeypatch.setattr(config, "O365_MAILBOX", "")
    assert integrations.send_email("to@example.com", "subject", "body") is False


def test_send_email_success_posts_expected_payload(monkeypatch):
    import config
    monkeypatch.setattr(config, "O365_CLIENT_ID", "client-id")
    monkeypatch.setattr(config, "O365_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(config, "O365_TENANT_ID", "tenant-id")
    monkeypatch.setattr(config, "O365_MAILBOX", "klm@smtw.in")

    monkeypatch.setattr(integrations, "_get_graph_token", lambda: "fake-token")

    calls = []

    def _fake_http_json(url, *, method="GET", headers=None, body=None, timeout=15):
        calls.append((url, method, headers, body))
        return {}

    monkeypatch.setattr(integrations, "_http_json", _fake_http_json)

    assert integrations.send_email("to@example.com", "subject", "body text") is True
    assert len(calls) == 1
    url, method, headers, body = calls[0]
    assert url == "https://graph.microsoft.com/v1.0/users/klm@smtw.in/sendMail"
    assert method == "POST"
    assert headers == {"Authorization": "Bearer fake-token"}
    assert body["message"]["subject"] == "subject"
    assert body["message"]["body"] == {"contentType": "Text", "content": "body text"}
    assert body["message"]["toRecipients"] == [{"emailAddress": {"address": "to@example.com"}}]


def test_send_email_uses_html_body_when_provided(monkeypatch):
    import config
    monkeypatch.setattr(config, "O365_CLIENT_ID", "client-id")
    monkeypatch.setattr(config, "O365_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(config, "O365_TENANT_ID", "tenant-id")
    monkeypatch.setattr(config, "O365_MAILBOX", "klm@smtw.in")

    monkeypatch.setattr(integrations, "_get_graph_token", lambda: "fake-token")

    captured = {}

    def _fake_http_json(url, *, method="GET", headers=None, body=None, timeout=15):
        captured.update(body=body)
        return {}

    monkeypatch.setattr(integrations, "_http_json", _fake_http_json)

    assert integrations.send_email(
        "to@example.com", "subject", "plain fallback", body_html="<p>hi</p>"
    ) is True
    assert captured["body"]["message"]["body"] == {"contentType": "HTML", "content": "<p>hi</p>"}


def test_send_email_returns_false_on_error(monkeypatch):
    import config
    monkeypatch.setattr(config, "O365_CLIENT_ID", "client-id")
    monkeypatch.setattr(config, "O365_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(config, "O365_TENANT_ID", "tenant-id")
    monkeypatch.setattr(config, "O365_MAILBOX", "klm@smtw.in")

    def _raise(*args, **kwargs):
        raise RuntimeError("token acquisition failed")

    monkeypatch.setattr(integrations, "_get_graph_token", _raise)

    assert integrations.send_email("to@example.com", "subject", "body") is False
