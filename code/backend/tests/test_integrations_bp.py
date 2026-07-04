"""
HTTP tests for the Integrations blueprint.
External network calls are monkeypatched so no real credentials are needed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# GET /api/integrations/status
# ---------------------------------------------------------------------------

def test_status_all_unconfigured(client, monkeypatch):
    # /status reads config.O365_* directly — monkeypatch rather than rely on
    # secrets_app.py being blank, since dev machines have real values filled in.
    import config
    monkeypatch.setattr(config, "O365_CLIENT_ID", "")
    monkeypatch.setattr(config, "O365_CLIENT_SECRET", "")
    monkeypatch.setattr(config, "O365_TENANT_ID", "")
    monkeypatch.setattr(config, "O365_MAILBOX", "")

    resp = client.get("/api/integrations/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["email"] is False


# ---------------------------------------------------------------------------
# POST /api/integrations/email
# ---------------------------------------------------------------------------

def test_email_queued(client):
    resp = client.post("/api/integrations/email", json={
        "to": "a@b.com", "subject": "Hi", "body": "Body text",
    })
    assert resp.status_code == 202
    data = resp.get_json()
    assert "task_id" in data
    assert data["task_id"] > 0


def test_email_missing_fields(client):
    resp = client.post("/api/integrations/email", json={"to": "a@b.com"})
    assert resp.status_code == 400
