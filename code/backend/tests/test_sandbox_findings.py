"""
Regression tests for the 2026-09-29 attack-sandbox findings (tooling/sandbox/):

- GET /api/projects/content read .md files under db/ (its guard lacked the
  db/ check every other guard had)
- every corpus write path accepted .git/ targets (.git/hooks/pre-commit.md)
- grep: `(a+)+$` pinned the single gunicorn worker for 20s on a 29-char line
- send_email: endswith("@smtw.in") accepted "attacker@evil,me@smtw.in"
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


@pytest.fixture()
def conn(app):
    import local_db
    return local_db.get_db()


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    import config
    root = str(tmp_path / "data")
    _write(os.path.join(root, "VIT", "proj.md"), "# Proj\n")
    _write(os.path.join(root, "db", "leak.md"), "db-secret\n")
    _write(os.path.join(root, ".git", "notes.md"), "git-internal\n")
    monkeypatch.setattr(config, "USER_DATA_ROOT", root)
    return root


def _tool(conn, name):
    import tools_registry
    return next(t for t in tools_registry.build_tools(conn) if t.name == name).handler


# ---------------------------------------------------------------------------
# Reserved paths — db/ and dot-directories
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel, reserved", [
    ("VIT/proj.md", False),
    ("inbox.md", False),
    (".hidden-note.md", False),          # a dotfile is fine; a dot-DIRECTORY is not
    ("db/leak.md", True),
    ("db/sqlite/x.md", True),
    (".git/hooks/pre-commit.md", True),
    ("VIT/.obsidian/workspace.md", True),
])
def test_is_reserved_corpus_path(tmp_path, rel, reserved):
    import md_editor
    root = str(tmp_path)
    assert md_editor.is_reserved_corpus_path(root, os.path.join(root, *rel.split("/"))) is reserved


@pytest.mark.parametrize("rel", ["db/leak.md", "./db/leak.md", "VIT/../db/leak.md", ".git/notes.md"])
def test_projects_content_refuses_reserved_paths(client, corpus, rel):
    resp = client.get(f"/api/projects/content?path={rel}")
    assert resp.status_code == 400
    assert b"db-secret" not in resp.data and b"git-internal" not in resp.data


def test_projects_content_still_reads_normal_files(client, corpus):
    resp = client.get("/api/projects/content?path=VIT/proj.md")
    assert resp.status_code == 200


@pytest.mark.parametrize("rel", [".git/hooks/pre-commit.md", ".git/config.md", "VIT/.git/x.md"])
def test_validate_path_refuses_dot_directories(corpus, rel):
    import md_editor
    with pytest.raises(ValueError):
        md_editor.validate_path(rel)


def test_ai_read_and_list_skip_dot_directories(conn, corpus):
    assert _tool(conn, "read_file")({"path": ".git/notes.md"}).startswith("[error:")
    assert ".git" not in _tool(conn, "list_files")({})
    assert _tool(conn, "list_files")({"path": ".git"}).startswith("[error:")
    assert _tool(conn, "grep")({"pattern": "git-internal"}).startswith("No matches")


# ---------------------------------------------------------------------------
# grep ReDoS
# ---------------------------------------------------------------------------

def test_grep_catastrophic_pattern_is_time_boxed(conn, corpus, monkeypatch):
    import tools_registry
    monkeypatch.setattr(tools_registry, "_GREP_TIME_BUDGET_SEC", 0.5)
    _write(os.path.join(corpus, "VIT", "redos.md"), "a" * 40 + "!\n")
    t0 = time.monotonic()
    out = _tool(conn, "grep")({"pattern": "(a|aa)+$"})
    assert time.monotonic() - t0 < 5
    assert out.startswith("[error:") and "too expensive" in out


def test_grep_still_finds_normal_matches(conn, corpus):
    assert "VIT/proj.md:1" in _tool(conn, "grep")({"pattern": r"^# pro"})


# ---------------------------------------------------------------------------
# send_email recipient allowlist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("to", [
    "attacker@evil.example,owner@smtw.in",
    "attacker@evil.example;owner@smtw.in",
    "attacker@evil.example\nowner@smtw.in",
    "attacker@evil.example owner@smtw.in",
    '"owner@smtw.in" <attacker@evil.example>',
    "attacker@evil.example",
    "",
])
def test_email_allowlist_rejects_bypass_forms(monkeypatch, to):
    import config
    import tools_registry
    monkeypatch.setattr(config, "USER_EMAIL", "owner@smtw.in")
    assert tools_registry._is_allowed_email_recipient(to) is False


@pytest.mark.parametrize("to", ["owner@smtw.in", "Colleague@SMTW.in", "me@gmail.com"])
def test_email_allowlist_accepts_single_allowed_address(monkeypatch, to):
    import config
    import tools_registry
    monkeypatch.setattr(config, "USER_EMAIL", "me@gmail.com")
    assert tools_registry._is_allowed_email_recipient(to) is True
