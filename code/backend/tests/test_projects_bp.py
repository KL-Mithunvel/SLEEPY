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
