"""
Tests for the Logs blueprint: reads the '## Log' section of <OU>/Daily/<date>.md
files (the root-level logs/ folder is legacy — see docs/CORPUS_SCHEMA.md).
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


def _daily_with_log(entry_text: str) -> str:
    return (
        "---\ndate: 2026-06-18 Thursday\n---\n\n"
        "## Tasks\n\n- [ ] Some task\n\n"
        "## Daily checklist\n\n\n\n"
        f"## Log\n\n### Morning\n\n### Evening\n\n{entry_text}\n"
    )


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


def test_list_logs_skips_daily_files_with_no_log_content(client, monkeypatch, tmp_path):
    """A Daily file whose ## Log section is just bare Morning/Evening headers shouldn't show up."""
    import config

    root = tmp_path / "corpus"
    daily_dir = root / "VIT" / "Daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-06-18.md").write_text(
        "---\ndate: 2026-06-18 Thursday\n---\n\n## Tasks\n\n## Log\n\n### Morning\n\n### Evening\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs")
    assert resp.get_json()["total"] == 0


def test_list_logs_includes_daily_files_with_real_log_content(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus2"
    daily_dir = root / "VIT" / "Daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-06-18.md").write_text(
        _daily_with_log("Talked to Arun Kumar Sir about the CAN protocol setup."), encoding="utf-8",
    )

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs")
    data = resp.get_json()
    assert data["total"] == 1
    assert data["logs"][0]["ou"] == "VIT"
    assert data["logs"][0]["rel_path"] == "VIT/Daily/2026-06-18.md"
    assert data["logs"][0]["type"] == "daily"


def test_list_logs_sorted_newest_first_across_ous(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus3"
    for ou, d in [("Personal", "2026-06-01"), ("VIT", "2026-06-18"), ("SMTW", "2026-06-10")]:
        daily_dir = root / ou / "Daily"
        daily_dir.mkdir(parents=True)
        (daily_dir / f"{d}.md").write_text(_daily_with_log("Entry."), encoding="utf-8")

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs")
    dates = [l["date"] for l in resp.get_json()["logs"]]
    assert dates == ["18-06-2026", "10-06-2026", "01-06-2026"]


def test_list_logs_display_date_format(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus4"
    daily_dir = root / "SMTW" / "Daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-06-18.md").write_text(_daily_with_log("Shipped the OU reorg."), encoding="utf-8")

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs")
    log = resp.get_json()["logs"][0]
    assert log["date"] == "18-06-2026"


# ---------------------------------------------------------------------------
# GET /api/logs/content
# ---------------------------------------------------------------------------

def test_log_content_success_returns_only_log_section(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus5"
    daily_dir = root / "SMTW" / "Daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-06-18.md").write_text(
        "---\ndate: 2026-06-18 Thursday\n---\n\n## Tasks\n\n- [ ] Secret task text\n\n"
        "## Log\n\n### Evening\n\nStood up the server.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs/content?path=SMTW/Daily/2026-06-18.md")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "Stood up the server" in data["content"]
    assert "Secret task text" not in data["content"]
    assert data["rel_path"] == "SMTW/Daily/2026-06-18.md"


def test_log_content_missing_param(client):
    resp = client.get("/api/logs/content")
    assert resp.status_code == 400


def test_log_content_traversal_rejected(client, monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path / "root"))
    resp = client.get("/api/logs/content?path=../../etc/passwd")
    assert resp.status_code == 400


def test_log_content_rejects_non_daily_files(client, monkeypatch, tmp_path):
    """A project file (not a <OU>/Daily/<date>.md) must be rejected even if it exists."""
    import config

    root = tmp_path / "corpus6"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "proj.md").write_text("# Proj\n", encoding="utf-8")

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs/content?path=SMTW/proj.md")
    assert resp.status_code == 400


def test_log_content_not_found(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus7"
    (root / "SMTW" / "Daily").mkdir(parents=True)
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/logs/content?path=SMTW/Daily/2026-01-01.md")
    assert resp.status_code == 404
