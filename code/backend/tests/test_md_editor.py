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


# ---------------------------------------------------------------------------
# Secret scanning — corpus governance ("no secrets in MD files")
# ---------------------------------------------------------------------------

def test_scan_diff_for_secrets_detects_aws_key():
    diff = "--- a/x.md\n+++ b/x.md\n@@ -0,0 +1 @@\n+key = AKIAABCDEFGHIJKLMNOP\n"
    hits = md_editor.scan_diff_for_secrets(diff)
    assert "AWS access key" in hits


def test_scan_diff_for_secrets_detects_anthropic_key():
    diff = "+++ b/x.md\n+ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz\n"
    hits = md_editor.scan_diff_for_secrets(diff)
    assert "Anthropic API key" in hits


def test_scan_diff_for_secrets_detects_private_key_block():
    diff = "+++ b/x.md\n+-----BEGIN RSA PRIVATE KEY-----\n+MIIEpAIBAAKCAQEA...\n"
    hits = md_editor.scan_diff_for_secrets(diff)
    assert "private key block" in hits


def test_scan_diff_for_secrets_ignores_removed_lines():
    """A secret being *removed* from a file must not block the edit."""
    diff = "--- a/x.md\n+++ b/x.md\n@@ -1 +1 @@\n-key = AKIAABCDEFGHIJKLMNOP\n+key = REDACTED\n"
    hits = md_editor.scan_diff_for_secrets(diff)
    assert hits == []


def test_scan_diff_for_secrets_ignores_file_header_lines():
    """The '+++ b/...' file-header line itself must never be scanned as an
    added line, even if a path happened to look secret-shaped."""
    diff = "--- a/x.md\n+++ b/AKIAABCDEFGHIJKLMNOP.md\n@@ -0,0 +1 @@\n+hello\n"
    hits = md_editor.scan_diff_for_secrets(diff)
    assert hits == []


def test_scan_diff_for_secrets_clean_diff_is_empty():
    diff = "--- a/x.md\n+++ b/x.md\n@@ -0,0 +1 @@\n+Just a normal note about the project.\n"
    assert md_editor.scan_diff_for_secrets(diff) == []


def test_propose_edit_blocked_by_secret_in_new_content(data_root, db):
    with pytest.raises(ValueError, match="Blocked.*secret"):
        md_editor.propose_edit("OU/leak.md", "# Notes\nkey = AKIAABCDEFGHIJKLMNOP\n", "add key", db)
    # No pending ai_events row should have been written
    row = db.execute("SELECT COUNT(*) AS n FROM ai_events").fetchone()
    assert row["n"] == 0
    assert not os.path.exists(os.path.join(data_root, "OU", "leak.md"))


# ---------------------------------------------------------------------------
# Move
# ---------------------------------------------------------------------------

def test_validate_move_rejects_same_path(data_root):
    with pytest.raises(ValueError, match="same path"):
        md_editor.validate_move("SMTW/x.md", "SMTW/x.md")


def test_propose_move_missing_source(data_root, db):
    with pytest.raises(ValueError, match="does not exist"):
        md_editor.propose_move("SMTW/ghost.md", "VIT/ghost.md", "move", db)


def test_propose_move_existing_destination(data_root, db):
    for rel in ("SMTW/a.md", "VIT/a.md"):
        path = os.path.join(data_root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("# A")

    with pytest.raises(ValueError, match="already exists"):
        md_editor.propose_move("SMTW/a.md", "VIT/a.md", "move", db)


def test_apply_move_relocates_file_and_commits(data_root, db):
    import git

    repo = git.Repo.init(data_root)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()

    src = os.path.join(data_root, "SMTW", "wrong-ou.md")
    os.makedirs(os.path.dirname(src))
    with open(src, "w") as f:
        f.write("# Project\nContent")
    repo.index.add(["SMTW/wrong-ou.md"])
    repo.index.commit("seed")

    result = md_editor.propose_move("SMTW/wrong-ou.md", "VIT/wrong-ou.md", "fix OU", db)
    sha = md_editor.apply_move(result["event_id"], db)

    assert len(sha) == 40
    assert not os.path.exists(src)
    dst = os.path.join(data_root, "VIT", "wrong-ou.md")
    assert os.path.isfile(dst)
    with open(dst) as f:
        assert f.read() == "# Project\nContent"

    row = db.execute("SELECT accepted FROM ai_events WHERE id = ?", (result["event_id"],)).fetchone()
    assert row["accepted"] == 1


def test_apply_move_missing_event(data_root, db):
    with pytest.raises(ValueError, match="No pending"):
        md_editor.apply_move(9999, db)


def test_apply_move_conflict_when_source_changed(data_root, db):
    import git

    repo = git.Repo.init(data_root)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()

    src = os.path.join(data_root, "SMTW", "note.md")
    os.makedirs(os.path.dirname(src))
    with open(src, "w") as f:
        f.write("# Original")
    repo.index.add(["SMTW/note.md"])
    repo.index.commit("seed")

    result = md_editor.propose_move("SMTW/note.md", "VIT/note.md", "move", db)

    with open(src, "w") as f:
        f.write("# Changed after proposal")

    with pytest.raises(md_editor.EditConflict):
        md_editor.apply_move(result["event_id"], db)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def test_propose_delete_missing_file(data_root, db):
    with pytest.raises(ValueError, match="does not exist"):
        md_editor.propose_delete("SMTW/ghost.md", "delete", db)


def test_propose_delete_diff_shows_removed_content(data_root, db):
    path = os.path.join(data_root, "SMTW", "dup.md")
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as f:
        f.write("# Dup\nSome content")

    result = md_editor.propose_delete("SMTW/dup.md", "remove orphan duplicate", db)
    assert "-Some content" in result["diff"]
    assert result["op"] == "delete"


def test_apply_delete_removes_file_and_commits(data_root, db):
    import git

    repo = git.Repo.init(data_root)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()

    path = os.path.join(data_root, "SMTW", "dup.md")
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as f:
        f.write("# Dup\nContent")
    repo.index.add(["SMTW/dup.md"])
    repo.index.commit("seed")

    result = md_editor.propose_delete("SMTW/dup.md", "remove orphan duplicate", db)
    sha = md_editor.apply_delete(result["event_id"], db)

    assert len(sha) == 40
    assert not os.path.exists(path)

    row = db.execute("SELECT accepted FROM ai_events WHERE id = ?", (result["event_id"],)).fetchone()
    assert row["accepted"] == 1


def test_apply_delete_missing_event(data_root, db):
    with pytest.raises(ValueError, match="No pending"):
        md_editor.apply_delete(9999, db)


def test_reject_delete_does_not_touch_file(data_root, db):
    path = os.path.join(data_root, "SMTW", "keep.md")
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as f:
        f.write("# Keep")

    result = md_editor.propose_delete("SMTW/keep.md", "delete", db)
    md_editor.reject_edit(result["event_id"], db)

    assert os.path.isfile(path)
    row = db.execute("SELECT accepted FROM ai_events WHERE id = ?", (result["event_id"],)).fetchone()
    assert row["accepted"] == 0


# ---------------------------------------------------------------------------
# apply_pending — generic dispatcher
# ---------------------------------------------------------------------------

def test_apply_pending_dispatches_write(data_root, db):
    import git
    repo = git.Repo.init(data_root)
    repo.config_writer().set_value("user", "name", "T").release()
    repo.config_writer().set_value("user", "email", "t@t.com").release()

    result = md_editor.propose_edit("p.md", "# P\nContent", "test", db)
    sha = md_editor.apply_pending(result["event_id"], db)
    assert len(sha) == 40
    assert os.path.isfile(os.path.join(data_root, "p.md"))


def test_apply_pending_dispatches_move(data_root, db):
    import git
    repo = git.Repo.init(data_root)
    repo.config_writer().set_value("user", "name", "T").release()
    repo.config_writer().set_value("user", "email", "t@t.com").release()

    src = os.path.join(data_root, "SMTW", "m.md")
    os.makedirs(os.path.dirname(src))
    with open(src, "w") as f:
        f.write("# M")
    repo.index.add(["SMTW/m.md"])
    repo.index.commit("seed")

    result = md_editor.propose_move("SMTW/m.md", "VIT/m.md", "move", db)
    sha = md_editor.apply_pending(result["event_id"], db)
    assert len(sha) == 40
    assert os.path.isfile(os.path.join(data_root, "VIT", "m.md"))


def test_apply_pending_dispatches_delete(data_root, db):
    import git
    repo = git.Repo.init(data_root)
    repo.config_writer().set_value("user", "name", "T").release()
    repo.config_writer().set_value("user", "email", "t@t.com").release()

    path = os.path.join(data_root, "d.md")
    with open(path, "w") as f:
        f.write("# D")
    repo.index.add(["d.md"])
    repo.index.commit("seed")

    result = md_editor.propose_delete("d.md", "delete", db)
    sha = md_editor.apply_pending(result["event_id"], db)
    assert len(sha) == 40
    assert not os.path.exists(path)


def test_apply_pending_missing_event(data_root, db):
    with pytest.raises(ValueError, match="No pending"):
        md_editor.apply_pending(9999, db)


def test_propose_edit_allows_unrelated_edit_when_secret_already_present(data_root, db):
    """A pre-existing false positive elsewhere in the file must not block an
    unrelated edit to that same file — only newly *added* lines are scanned."""
    import git
    repo = git.Repo.init(data_root)
    repo.config_writer().set_value("user", "name", "T").release()
    repo.config_writer().set_value("user", "email", "t@t.com").release()

    path = os.path.join(data_root, "OU", "existing.md")
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Notes\nAKIAABCDEFGHIJKLMNOP appears here already, unrelated.\n")

    result = md_editor.propose_edit(
        "OU/existing.md",
        "# Notes\nAKIAABCDEFGHIJKLMNOP appears here already, unrelated.\n- [ ] a new task\n",
        "add a task",
        db,
    )
    md_editor.apply_edit(result["event_id"], db)
    with open(path, encoding="utf-8") as f:
        assert "a new task" in f.read()
