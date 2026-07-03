"""
HTTP tests for the Today blueprint routes.
External calls (LLM, ChromaDB, git) are monkeypatched.
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
# GET /api/today
# ---------------------------------------------------------------------------

def test_get_today_empty_tasks(client, monkeypatch):
    import task_scan
    monkeypatch.setattr(task_scan, "scan_todays_tasks", lambda data_root: [])
    resp = client.get("/api/today")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "tasks" in data
    assert data["tasks"] == []
    assert "briefing" in data


def test_get_today_returns_tasks(client, monkeypatch):
    import task_scan
    fake_tasks = [
        {"rel_path": "SMTW/Daily/2026-07-04.md", "text": "Deploy server", "ou": "SMTW", "due": "2026-07-05"},
        {"rel_path": "SMTW/Daily/2026-07-04.md", "text": "Review PR", "ou": "SMTW", "due": None},
    ]
    monkeypatch.setattr(task_scan, "scan_todays_tasks", lambda data_root: fake_tasks)
    resp = client.get("/api/today")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["ou"] == "SMTW"


def test_get_today_task_scan_failure_returns_empty(client, monkeypatch):
    import task_scan
    monkeypatch.setattr(task_scan, "scan_todays_tasks", lambda data_root: (_ for _ in ()).throw(RuntimeError("scan failed")))
    resp = client.get("/api/today")
    assert resp.status_code == 200
    assert resp.get_json()["tasks"] == []


# ---------------------------------------------------------------------------
# POST /api/today/briefing
# ---------------------------------------------------------------------------

def test_generate_briefing_success(client, monkeypatch):
    import ai_client
    monkeypatch.setattr(
        ai_client, "generate_morning_briefing",
        lambda conn: "Focus: Alpha project deployment today."
    )
    resp = client.post("/api/today/briefing", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["briefing"] == "Focus: Alpha project deployment today."
    assert "generated_at" in data


def test_generate_briefing_llm_failure(client, monkeypatch):
    import ai_client
    monkeypatch.setattr(
        ai_client, "generate_morning_briefing",
        lambda conn: (_ for _ in ()).throw(RuntimeError("API error"))
    )
    resp = client.post("/api/today/briefing", json={})
    assert resp.status_code == 502
    assert "error" in resp.get_json()


# ---------------------------------------------------------------------------
# POST /api/today/capture
# ---------------------------------------------------------------------------

def test_capture_missing_text(client):
    resp = client.post("/api/today/capture", json={})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_capture_empty_text(client):
    resp = client.post("/api/today/capture", json={"text": "   "})
    assert resp.status_code == 400


def test_capture_success(client, monkeypatch, tmp_path):
    import config
    import md_editor

    data_root = str(tmp_path / "corpus")
    os.makedirs(data_root)
    monkeypatch.setattr(config, "USER_DATA_ROOT", data_root)

    monkeypatch.setattr(
        md_editor, "propose_edit",
        lambda rel, content, summary, conn: {
            "event_id": 77, "diff": "+line", "rel_path": rel,
            "summary": summary, "is_new": True,
        }
    )
    monkeypatch.setattr(
        md_editor, "apply_edit",
        lambda event_id, conn: "abcdef1234567890abcdef1234567890abcdef12"
    )

    resp = client.post("/api/today/capture", json={"text": "Write the quarterly report"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "quarterly report" in data["line"]
    assert data["sha"] == "abcdef12"


def test_capture_creates_inbox_header_for_new_file(client, monkeypatch, tmp_path):
    """When inbox.md doesn't exist the new content starts with the standard header."""
    import config
    import md_editor

    data_root = str(tmp_path / "corpus2")
    os.makedirs(data_root)
    monkeypatch.setattr(config, "USER_DATA_ROOT", data_root)

    captured_content = {}

    def fake_propose(rel, content, summary, conn):
        captured_content["content"] = content
        return {"event_id": 1, "diff": "", "rel_path": rel, "summary": summary, "is_new": True}

    monkeypatch.setattr(md_editor, "propose_edit", fake_propose)
    monkeypatch.setattr(
        md_editor, "apply_edit",
        lambda event_id, conn: "a" * 40
    )

    client.post("/api/today/capture", json={"text": "First thought"})
    assert "# Inbox" in captured_content["content"]
    assert "First thought" in captured_content["content"]


def test_capture_appends_to_existing_inbox(client, monkeypatch, tmp_path):
    """When inbox.md already exists the new line is appended, header is NOT duplicated."""
    import config
    import md_editor

    data_root = str(tmp_path / "corpus3")
    os.makedirs(data_root)
    monkeypatch.setattr(config, "USER_DATA_ROOT", data_root)

    inbox = os.path.join(data_root, "inbox.md")
    with open(inbox, "w") as f:
        f.write("# Inbox\n\nExisting item.\n")

    captured_content = {}

    def fake_propose(rel, content, summary, conn):
        captured_content["content"] = content
        return {"event_id": 2, "diff": "", "rel_path": rel, "summary": summary, "is_new": False}

    monkeypatch.setattr(md_editor, "propose_edit", fake_propose)
    monkeypatch.setattr(md_editor, "apply_edit", lambda eid, conn: "b" * 40)

    client.post("/api/today/capture", json={"text": "New thought"})
    content = captured_content["content"]
    assert content.count("# Inbox") == 1   # header not duplicated
    assert "Existing item." in content
    assert "New thought" in content


# ---------------------------------------------------------------------------
# POST /api/today/tasks/toggle
# ---------------------------------------------------------------------------

def test_toggle_task_missing_fields(client):
    resp = client.post("/api/today/tasks/toggle", json={})
    assert resp.status_code == 400


def test_toggle_task_success(client, monkeypatch, tmp_path):
    import config

    data_root = str(tmp_path / "corpus_toggle")
    proj_dir = os.path.join(data_root, "SMTW")
    os.makedirs(proj_dir)
    proj_file = os.path.join(proj_dir, "proj.md")
    with open(proj_file, "w", encoding="utf-8") as f:
        f.write("---\nkey: proj\nstatus: active\n---\n\n## Tasks\n\n- [ ] Deploy server\n")
    monkeypatch.setattr(config, "USER_DATA_ROOT", data_root)

    resp = client.post("/api/today/tasks/toggle", json={"rel_path": "SMTW/proj.md", "text": "Deploy server"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    with open(proj_file, encoding="utf-8") as f:
        content = f.read()
    assert "- [x] Deploy server" in content
    assert "- [ ] Deploy server" not in content


def test_toggle_task_not_found(client, monkeypatch, tmp_path):
    import config

    data_root = str(tmp_path / "corpus_toggle2")
    proj_dir = os.path.join(data_root, "SMTW")
    os.makedirs(proj_dir)
    with open(os.path.join(proj_dir, "proj.md"), "w", encoding="utf-8") as f:
        f.write("---\nkey: proj\nstatus: active\n---\n\n- [ ] Deploy server\n")
    monkeypatch.setattr(config, "USER_DATA_ROOT", data_root)

    resp = client.post("/api/today/tasks/toggle", json={"rel_path": "SMTW/proj.md", "text": "Nonexistent task"})
    assert resp.status_code == 404
