"""
Tests for md_editor: path validation, diff computation, propose/apply/reject flow.
"""

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import md_editor
from md_editor import validate_edit, compute_diff


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    root = str(tmp_path / "data")
    os.makedirs(root)
    monkeypatch.setattr(config, "USER_DATA_ROOT", root)
    return root


@pytest.fixture()
def db(data_root):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE ai_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            prompt_hash TEXT,
            model TEXT,
            diff TEXT,
            result TEXT,
            accepted INTEGER,
            voided INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER,
            input_tokens INTEGER,
            output_tokens INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# validate_edit — accept
# ---------------------------------------------------------------------------

def test_validate_edit_valid_path(data_root):
    validate_edit("SMTW/project.md", "# Hello\nContent")


def test_validate_edit_nested_path(data_root):
    validate_edit("OU1/OU2/project.md", "# Test")


# ---------------------------------------------------------------------------
# validate_edit — reject
# ---------------------------------------------------------------------------

def test_validate_edit_path_traversal(data_root):
    with pytest.raises(ValueError, match="traversal"):
        validate_edit("../../etc/passwd.md", "evil")


def test_validate_edit_path_traversal_absolute(data_root):
    with pytest.raises(ValueError, match="traversal"):
        validate_edit("/etc/passwd", "evil")


def test_validate_edit_db_dir(data_root):
    with pytest.raises(ValueError, match="db/"):
        validate_edit("db/chroma/data.md", "data")


def test_validate_edit_non_md(data_root):
    with pytest.raises(ValueError, match=r"\.md"):
        validate_edit("SMTW/script.py", "import os")


def test_validate_edit_empty_content(data_root):
    with pytest.raises(ValueError, match="empty"):
        validate_edit("SMTW/project.md", "   ")


def test_validate_edit_empty_path(data_root):
    with pytest.raises(ValueError, match="empty"):
        validate_edit("", "# Content")


def test_validate_edit_oversized(data_root):
    huge = "x" * (600 * 1024)
    with pytest.raises(ValueError, match="size limit"):
        validate_edit("SMTW/big.md", huge)


# ---------------------------------------------------------------------------
# compute_diff
# ---------------------------------------------------------------------------

def test_compute_diff_detects_change():
    diff = compute_diff("# Old\nLine 1\n", "# Old\nLine 1\nLine 2\n", "test.md")
    assert "+Line 2" in diff
    assert "a/test.md" in diff


def test_compute_diff_no_change():
    diff = compute_diff("# Same\n", "# Same\n", "test.md")
    assert diff == ""


def test_compute_diff_deletion():
    diff = compute_diff("# Head\nRemove me\n", "# Head\n", "test.md")
    assert "-Remove me" in diff


# ---------------------------------------------------------------------------
# propose / apply / reject
# ---------------------------------------------------------------------------

def test_propose_edit_new_file(data_root, db):
    result = md_editor.propose_edit("OU/new.md", "# New File\nContent", "Create new file", db)
    assert result["is_new"] is True
    assert result["event_id"] > 0
    assert result["rel_path"] == "OU/new.md"


def test_propose_edit_existing_file(data_root, db):
    target = os.path.join(data_root, "note.md")
    with open(target, "w") as f:
        f.write("# Original\nOld content")

    result = md_editor.propose_edit("note.md", "# Original\nNew content", "Update note", db)
    assert result["is_new"] is False
    assert "+New content" in result["diff"]
    assert "-Old content" in result["diff"]


def test_propose_edit_stores_pending_event(data_root, db):
    md_editor.propose_edit("p.md", "# P\nContent", "test", db)
    row = db.execute("SELECT * FROM ai_events WHERE event_type = 'md_edit'").fetchone()
    assert row is not None
    assert row["accepted"] is None


def test_apply_edit_writes_file_and_commits(data_root, db):
    import git

    # Init git repo so GitPython can commit
    repo = git.Repo.init(data_root)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()

    result = md_editor.propose_edit("newfile.md", "# New\nContent", "Add file", db)
    sha = md_editor.apply_edit(result["event_id"], db)

    assert os.path.isfile(os.path.join(data_root, "newfile.md"))
    assert len(sha) == 40  # git SHA

    row = db.execute("SELECT accepted FROM ai_events WHERE id = ?", (result["event_id"],)).fetchone()
    assert row["accepted"] == 1


def test_apply_edit_missing_event(data_root, db):
    with pytest.raises(ValueError, match="No pending"):
        md_editor.apply_edit(9999, db)


def test_reject_edit(data_root, db):
    result = md_editor.propose_edit("r.md", "# R\nContent", "reject test", db)
    md_editor.reject_edit(result["event_id"], db)

    row = db.execute("SELECT accepted FROM ai_events WHERE id = ?", (result["event_id"],)).fetchone()
    assert row["accepted"] == 0

    # File should NOT have been written
    assert not os.path.exists(os.path.join(data_root, "r.md"))


def test_apply_already_applied_raises(data_root, db):
    import git
    repo = git.Repo.init(data_root)
    repo.config_writer().set_value("user", "name", "T").release()
    repo.config_writer().set_value("user", "email", "t@t.com").release()

    result = md_editor.propose_edit("x.md", "# X\nHi", "test", db)
    md_editor.apply_edit(result["event_id"], db)

    with pytest.raises(ValueError, match="No pending"):
        md_editor.apply_edit(result["event_id"], db)
