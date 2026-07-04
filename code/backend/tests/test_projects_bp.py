"""
Tests for the Projects blueprint: list and content endpoints.
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
# GET /api/projects — empty corpus
# ---------------------------------------------------------------------------

def test_list_projects_empty(client, monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path / "empty"))
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["projects"] == []
    assert data["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/projects — with project files
# ---------------------------------------------------------------------------

def test_list_projects_with_files(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus"
    ou = root / "SMTW"
    ou.mkdir(parents=True)
    (ou / "alpha.md").write_text(
        "---\ntitle: Alpha Project\nstatus: Active\n---\n# Alpha Project\n\n- [ ] Deploy server\n- [x] Write tests\n",
        encoding="utf-8",
    )
    (ou / "beta.md").write_text("# Beta\n\n- [ ] Review PR\n", encoding="utf-8")

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 2

    alpha = next(p for p in data["projects"] if "alpha" in p["rel_path"])
    assert alpha["name"] == "Alpha Project"
    assert alpha["status"] == "Active"
    assert alpha["open_tasks"] == 1
    assert alpha["done_tasks"] == 1
    assert alpha["ou"] == "SMTW"


def test_list_projects_includes_subfolders_and_root_skips_only_db(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus2"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "proj.md").write_text("# Proj\n", encoding="utf-8")
    (root / "SMTW" / "Research").mkdir()
    (root / "SMTW" / "Research" / "soft-robotics.md").write_text("# Soft Robotics\n", encoding="utf-8")
    (root / "logs").mkdir()
    (root / "logs" / "2026-06-18.md").write_text("# Log\n", encoding="utf-8")
    (root / "inbox.md").write_text("# Inbox\n", encoding="utf-8")
    (root / "db").mkdir()
    (root / "db" / "meta.md").write_text("# DB\n", encoding="utf-8")

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/projects")
    data = resp.get_json()
    paths = [p["rel_path"] for p in data["projects"]]
    assert any("SMTW/proj.md" == p for p in paths)
    assert any("SMTW/Research/soft-robotics.md" == p for p in paths)
    assert any("logs" in p for p in paths)
    assert "inbox.md" in paths
    assert not any("db" in p for p in paths)

    inbox = next(p for p in data["projects"] if p["rel_path"] == "inbox.md")
    assert inbox["ou"] == "General"


def test_list_projects_fallback_title_from_filename(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus3"
    (root / "OU1").mkdir(parents=True)
    (root / "OU1" / "my-project.md").write_text("No heading here.", encoding="utf-8")

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/projects")
    project = resp.get_json()["projects"][0]
    assert project["name"] == "My Project"


# ---------------------------------------------------------------------------
# GET /api/projects/content
# ---------------------------------------------------------------------------

def test_project_content_success(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus4"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "alpha.md").write_text("# Alpha\n\nContent here.", encoding="utf-8")

    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/projects/content?path=SMTW/alpha.md")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "Content here" in data["content"]
    assert data["rel_path"] == "SMTW/alpha.md"


def test_project_content_missing_param(client):
    resp = client.get("/api/projects/content")
    assert resp.status_code == 400


def test_project_content_traversal_rejected(client, monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path / "root"))
    resp = client.get("/api/projects/content?path=../../etc/passwd")
    assert resp.status_code == 400


def test_project_content_not_found(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "corpus5"
    (root / "SMTW").mkdir(parents=True)
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/projects/content?path=SMTW/missing.md")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/projects/content
# ---------------------------------------------------------------------------

def test_save_project_content_missing_path(client):
    resp = client.put("/api/projects/content", json={"content": "x"})
    assert resp.status_code == 400


def test_save_project_content_success(client, monkeypatch, tmp_path):
    import config
    import md_editor

    root = tmp_path / "corpus6"
    (root / "SMTW").mkdir(parents=True)
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    monkeypatch.setattr(
        md_editor, "propose_edit",
        lambda rel, content, summary, conn: {
            "event_id": 99, "diff": "+line", "rel_path": rel, "summary": summary, "is_new": False,
        }
    )
    monkeypatch.setattr(md_editor, "apply_edit", lambda event_id, conn: "a1b2c3d4e5f6" + "0" * 28)

    resp = client.put("/api/projects/content", json={"path": "SMTW/alpha.md", "content": "# Edited\n"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["sha"] == "a1b2c3d4"


def test_save_project_content_validation_error(client, monkeypatch, tmp_path):
    import config
    import md_editor

    root = tmp_path / "corpus7"
    (root / "SMTW").mkdir(parents=True)
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    def _boom(rel, content, summary, conn):
        raise ValueError("new_content is empty")
    monkeypatch.setattr(md_editor, "propose_edit", _boom)

    resp = client.put("/api/projects/content", json={"path": "SMTW/alpha.md", "content": ""})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Structured editor — shared fixtures/helpers
# ---------------------------------------------------------------------------

_PROJECT_MD = """---
key: alpha
status: active
owner: KL Mithunvel
started: 2026-07-01
---

# Alpha Project

## Goal

Ship the thing.

## Tasks

- [ ] First task
- [x] Done task priority:low

## Decisions

- 2026-07-01: Picked option A

## Open Questions

- Still not sure about X

## Notes

## AI Notes
"""


def _fake_md_editor_writing(monkeypatch, data_root):
    """propose_edit/apply_edit that actually persist new_content to disk (no git)."""
    import md_editor

    captured = {}

    def _propose(rel, content, summary, conn):
        captured["rel"] = rel
        captured["content"] = content
        return {"event_id": 1, "diff": "+x", "rel_path": rel, "summary": summary, "is_new": False}

    def _apply(event_id, conn):
        abs_path = os.path.join(data_root, captured["rel"])
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(captured["content"])
        return "0" * 40

    monkeypatch.setattr(md_editor, "propose_edit", _propose)
    monkeypatch.setattr(md_editor, "apply_edit", _apply)


# ---------------------------------------------------------------------------
# GET /api/projects/structured
# ---------------------------------------------------------------------------

def test_structured_parses_frontmatter_tasks_and_lists(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "s1"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "alpha.md").write_text(_PROJECT_MD, encoding="utf-8")
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.get("/api/projects/structured?path=SMTW/alpha.md")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["frontmatter"]["status"] == "active"
    assert data["frontmatter"]["started"] == "2026-07-01"
    assert len(data["tasks"]) == 2
    done_task = next(t for t in data["tasks"] if t["done"])
    assert done_task["priority"] == "low"
    assert data["decisions"] == ["2026-07-01: Picked option A"]
    assert data["open_questions"] == ["Still not sure about X"]


def test_structured_not_found(client, monkeypatch, tmp_path):
    import config
    root = tmp_path / "s2"
    root.mkdir()
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))
    resp = client.get("/api/projects/structured?path=SMTW/missing.md")
    assert resp.status_code == 404


def test_structured_invalid_path(client, monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path / "s3"))
    resp = client.get("/api/projects/structured?path=../../etc/passwd")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/projects/tasks
# ---------------------------------------------------------------------------

def test_add_task(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "t1"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "alpha.md").write_text(_PROJECT_MD, encoding="utf-8")
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))
    _fake_md_editor_writing(monkeypatch, str(root))

    resp = client.post("/api/projects/tasks", json={
        "path": "SMTW/alpha.md", "action": "add", "text": "New task", "priority": "high", "due": "2026-08-01",
    })
    assert resp.status_code == 200

    content = (root / "SMTW" / "alpha.md").read_text(encoding="utf-8")
    assert "- [ ] New task priority:high due:2026-08-01" in content


def test_edit_task_toggles_done_and_fields(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "t2"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "alpha.md").write_text(_PROJECT_MD, encoding="utf-8")
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))
    _fake_md_editor_writing(monkeypatch, str(root))

    resp = client.post("/api/projects/tasks", json={
        "path": "SMTW/alpha.md", "action": "edit",
        "old_line": "- [ ] First task", "text": "First task", "done": True, "priority": "medium",
    })
    assert resp.status_code == 200

    content = (root / "SMTW" / "alpha.md").read_text(encoding="utf-8")
    assert "- [x] First task priority:medium" in content


def test_remove_task(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "t3"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "alpha.md").write_text(_PROJECT_MD, encoding="utf-8")
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))
    _fake_md_editor_writing(monkeypatch, str(root))

    resp = client.post("/api/projects/tasks", json={
        "path": "SMTW/alpha.md", "action": "remove", "old_line": "- [ ] First task",
    })
    assert resp.status_code == 200

    content = (root / "SMTW" / "alpha.md").read_text(encoding="utf-8")
    assert "First task" not in content


def test_task_action_not_found_returns_400(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "t4"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "alpha.md").write_text(_PROJECT_MD, encoding="utf-8")
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))
    _fake_md_editor_writing(monkeypatch, str(root))

    resp = client.post("/api/projects/tasks", json={
        "path": "SMTW/alpha.md", "action": "remove", "old_line": "- [ ] Does not exist",
    })
    assert resp.status_code == 400


def test_task_unknown_action(client, monkeypatch, tmp_path):
    import config
    root = tmp_path / "t5"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "alpha.md").write_text(_PROJECT_MD, encoding="utf-8")
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.post("/api/projects/tasks", json={"path": "SMTW/alpha.md", "action": "bogus"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/projects/list-item
# ---------------------------------------------------------------------------

def test_add_decision(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "l1"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "alpha.md").write_text(_PROJECT_MD, encoding="utf-8")
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))
    _fake_md_editor_writing(monkeypatch, str(root))

    resp = client.post("/api/projects/list-item", json={
        "path": "SMTW/alpha.md", "section": "Decisions", "action": "add", "text": "New decision",
    })
    assert resp.status_code == 200
    content = (root / "SMTW" / "alpha.md").read_text(encoding="utf-8")
    assert "- New decision" in content


def test_remove_open_question(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "l2"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "alpha.md").write_text(_PROJECT_MD, encoding="utf-8")
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))
    _fake_md_editor_writing(monkeypatch, str(root))

    resp = client.post("/api/projects/list-item", json={
        "path": "SMTW/alpha.md", "section": "Open Questions", "action": "remove", "text": "Still not sure about X",
    })
    assert resp.status_code == 200
    content = (root / "SMTW" / "alpha.md").read_text(encoding="utf-8")
    assert "Still not sure about X" not in content


# ---------------------------------------------------------------------------
# PUT /api/projects/section
# ---------------------------------------------------------------------------

def test_update_section_text(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "sec1"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "alpha.md").write_text(_PROJECT_MD, encoding="utf-8")
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))
    _fake_md_editor_writing(monkeypatch, str(root))

    resp = client.put("/api/projects/section", json={
        "path": "SMTW/alpha.md", "heading": "Goal", "text": "New goal text.",
    })
    assert resp.status_code == 200
    content = (root / "SMTW" / "alpha.md").read_text(encoding="utf-8")
    assert "New goal text." in content
    assert "Ship the thing." not in content


# ---------------------------------------------------------------------------
# PUT /api/projects/status
# ---------------------------------------------------------------------------

def test_status_change_no_move(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "st1"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "alpha.md").write_text(_PROJECT_MD, encoding="utf-8")
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))
    _fake_md_editor_writing(monkeypatch, str(root))

    resp = client.put("/api/projects/status", json={"path": "SMTW/alpha.md", "status": "on_hold"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["rel_path"] == "SMTW/alpha.md"

    content = (root / "SMTW" / "alpha.md").read_text(encoding="utf-8")
    assert "status: on_hold" in content


def test_status_archived_moves_file(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "st2"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "alpha.md").write_text(_PROJECT_MD, encoding="utf-8")
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.put("/api/projects/status", json={"path": "SMTW/alpha.md", "status": "archived"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["rel_path"] == "SMTW/Archive/alpha.md"

    assert not (root / "SMTW" / "alpha.md").exists()
    moved = root / "SMTW" / "Archive" / "alpha.md"
    assert moved.exists()
    assert "status: archived" in moved.read_text(encoding="utf-8")


def test_status_unarchive_moves_file_back(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "st3"
    (root / "SMTW" / "Archive").mkdir(parents=True)
    (root / "SMTW" / "Archive" / "alpha.md").write_text(
        _PROJECT_MD.replace("status: active", "status: archived"), encoding="utf-8",
    )
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.put("/api/projects/status", json={"path": "SMTW/Archive/alpha.md", "status": "active"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["rel_path"] == "SMTW/alpha.md"

    assert not (root / "SMTW" / "Archive" / "alpha.md").exists()
    assert (root / "SMTW" / "alpha.md").exists()


def test_status_invalid_value_rejected(client, monkeypatch, tmp_path):
    import config

    root = tmp_path / "st4"
    (root / "SMTW").mkdir(parents=True)
    (root / "SMTW" / "alpha.md").write_text(_PROJECT_MD, encoding="utf-8")
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))

    resp = client.put("/api/projects/status", json={"path": "SMTW/alpha.md", "status": "bogus"})
    assert resp.status_code == 400
