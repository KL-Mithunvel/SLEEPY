"""
Tests for the Logs blueprint: list and content endpoints.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# GET /api/logs — list
# ---------------------------------------------------------------------------

def test_list_logs_empty(client, monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path / "empty"))
    resp = client.get("/api/logs")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["logs"] == []
    assert data["total"] == 0


def test_list_logs_daily_and_weekly(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus"
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "2026-06-18.md").write_text("# 18 June 2026\n", encoding="utf-8")
    (logs_dir / "2026-06-17.md").write_text("# 17 June 2026\n", encoding="utf-8")
    (logs_dir / "2026-W25.md").write_text("# Week 25 2026\n", encoding="utf-8")

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 3

    types = {l["filename"]: l["type"] for l in data["logs"]}
    assert types["2026-06-18.md"] == "daily"
    assert types["2026-06-17.md"] == "daily"
    assert types["2026-W25.md"] == "weekly"


def test_list_logs_sorted_newest_first(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus2"
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "2026-06-01.md").write_text("# June 1\n", encoding="utf-8")
    (logs_dir / "2026-06-18.md").write_text("# June 18\n", encoding="utf-8")
    (logs_dir / "2026-06-10.md").write_text("# June 10\n", encoding="utf-8")

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs")
    filenames = [l["filename"] for l in resp.get_json()["logs"]]
    assert filenames == sorted(filenames, reverse=True)


def test_list_logs_daily_display_date(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus3"
    (root / "logs").mkdir(parents=True)
    (root / "logs" / "2026-06-18.md").write_text("# Log\n", encoding="utf-8")

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs")
    log = resp.get_json()["logs"][0]
    assert log["date"] == "18-06-2026"


def test_list_logs_weekly_display_date(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus4"
    (root / "logs").mkdir(parents=True)
    (root / "logs" / "2026-W25.md").write_text("# Week 25\n", encoding="utf-8")

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs")
    log = resp.get_json()["logs"][0]
    assert log["date"] == "W25 2026"
    assert log["type"] == "weekly"


# ---------------------------------------------------------------------------
# GET /api/logs/content
# ---------------------------------------------------------------------------

def test_log_content_success(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus5"
    (root / "logs").mkdir(parents=True)
    (root / "logs" / "2026-06-18.md").write_text("# 18 June\n\nStood up the server.", encoding="utf-8")

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs/content?path=logs/2026-06-18.md")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "Stood up the server" in data["content"]
    assert data["rel_path"] == "logs/2026-06-18.md"


def test_log_content_missing_param(client):
    resp = client.get("/api/logs/content")
    assert resp.status_code == 400


def test_log_content_traversal_rejected(client, monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path / "root"))
    resp = client.get("/api/logs/content?path=../../etc/passwd")
    assert resp.status_code == 400


def test_log_content_outside_logs_dir_rejected(client, monkeypatch, tmp_path):
    """A valid .md file outside logs/ must be rejected."""
    import config

    root = tmp_path / "corpus6"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "proj.md").write_text("# Proj\n", encoding="utf-8")
    (root / "logs").mkdir()

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs/content?path=SMTW/proj.md")
    assert resp.status_code == 400


def test_log_content_not_found(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus7"
    (root / "logs").mkdir(parents=True)
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs/content?path=logs/2026-01-01.md")
    assert resp.status_code == 404
