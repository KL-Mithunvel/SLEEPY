"""
Tests for offsite.py — nightly corpus push + SQLite snapshot replication
("Layer 5 — Backup & recovery", docs/SECURITY_REVIEW.md).

The push tests drive real git against a real bare repo in tmp_path rather than
mocking GitPython: the whole point of this module is that an actual push
reaches an actual remote, and a mocked `repo.git.push` would have passed just
as happily with the arguments wrong.
"""

import gzip
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")


# ---------------------------------------------------------------------------
# Fixtures — a real corpus repo with a real bare remote
# ---------------------------------------------------------------------------

@pytest.fixture()
def corpus(tmp_path):
    """A corpus repo with one commit and a bare remote named 'origin'."""
    import git

    remote_path = tmp_path / "remote.git"
    git.Repo.init(remote_path, bare=True)

    work = tmp_path / "corpus"
    work.mkdir()
    repo = git.Repo.init(work, initial_branch="main")
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test")
        cw.set_value("user", "email", "test@example.invalid")
    (work / "ABOUT.md").write_text("# corpus\n")
    repo.index.add(["ABOUT.md"])
    repo.index.commit("initial")
    repo.create_remote("origin", str(remote_path))

    return {"work": str(work), "remote": str(remote_path), "repo": repo}


@pytest.fixture()
def no_remote_corpus(tmp_path):
    """A corpus repo with no remote configured at all — the current reality."""
    import git

    work = tmp_path / "corpus_solo"
    work.mkdir()
    repo = git.Repo.init(work, initial_branch="main")
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test")
        cw.set_value("user", "email", "test@example.invalid")
    (work / "ABOUT.md").write_text("# corpus\n")
    repo.index.add(["ABOUT.md"])
    repo.index.commit("initial")
    return str(work)


@pytest.fixture(autouse=True)
def offsite_defaults(monkeypatch):
    import config
    monkeypatch.setattr(config, "OFFSITE_PUSH_ENABLED", "auto")
    monkeypatch.setattr(config, "CORPUS_GIT_REMOTE", "origin")
    monkeypatch.setattr(config, "CORPUS_GIT_BRANCH", "")
    monkeypatch.setattr(config, "OFFSITE_SSH_KEY_PATH", "")
    monkeypatch.setattr(config, "OFFSITE_SNAPSHOT_DIR", "")
    monkeypatch.setattr(config, "DB_BACKUP_RETENTION_DAYS", 14)


def _remote_head(remote_path, branch="main"):
    import git
    return git.Repo(remote_path).commit(branch).hexsha


# ---------------------------------------------------------------------------
# push_corpus — the happy path actually moves commits
# ---------------------------------------------------------------------------

def test_push_corpus_pushes_commits_to_remote(corpus):
    import offsite

    result = offsite.push_corpus(corpus["work"])

    assert result["pushed"] is True
    assert result["branch"] == "main"
    assert _remote_head(corpus["remote"]) == corpus["repo"].head.commit.hexsha


def test_push_corpus_ships_new_commits_on_a_later_run(corpus):
    import offsite

    offsite.push_corpus(corpus["work"])

    repo = corpus["repo"]
    (os.path.join(corpus["work"], "second.md"))
    with open(os.path.join(corpus["work"], "second.md"), "w") as f:
        f.write("# later work\n")
    repo.index.add(["second.md"])
    new_sha = repo.index.commit("second").hexsha

    offsite.push_corpus(corpus["work"])
    assert _remote_head(corpus["remote"]) == new_sha


def test_push_corpus_honours_explicit_branch_override(corpus, monkeypatch):
    import config
    import offsite
    monkeypatch.setattr(config, "CORPUS_GIT_BRANCH", "main")

    result = offsite.push_corpus(corpus["work"])
    assert result["pushed"] is True
    assert result["branch"] == "main"


def test_push_corpus_releases_the_corpus_lock(corpus):
    """A push that holds the lock forever would wedge every later MD write."""
    import md_editor
    import offsite

    offsite.push_corpus(corpus["work"])
    lock = os.path.join(corpus["work"], md_editor._GIT_LOCK_NAME)
    assert not os.path.exists(lock)


# ---------------------------------------------------------------------------
# push_corpus — unconfigured cases
# ---------------------------------------------------------------------------

def test_auto_mode_skips_quietly_when_no_remote(no_remote_corpus):
    """Today's state: no remote anywhere. Must not fail the nightly job."""
    import offsite

    result = offsite.push_corpus(no_remote_corpus)
    assert result["pushed"] is False
    assert "no remote" in result["reason"]


def test_enabled_mode_raises_when_no_remote(no_remote_corpus, monkeypatch):
    """Explicitly enabled means "tell me when this does not work"."""
    import config
    import offsite
    monkeypatch.setattr(config, "OFFSITE_PUSH_ENABLED", "1")

    with pytest.raises(RuntimeError, match="no remote"):
        offsite.push_corpus(no_remote_corpus)


def test_off_mode_skips_even_with_a_working_remote(corpus, monkeypatch):
    import config
    import offsite
    monkeypatch.setattr(config, "OFFSITE_PUSH_ENABLED", "0")

    assert offsite.push_corpus(corpus["work"])["reason"] == "disabled"


def test_auto_mode_skips_when_path_is_not_a_git_repo(tmp_path):
    import offsite

    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    result = offsite.push_corpus(str(plain))
    assert result["pushed"] is False
    assert "no git repo" in result["reason"]


def test_enabled_mode_raises_when_path_is_not_a_git_repo(tmp_path, monkeypatch):
    import config
    import offsite
    monkeypatch.setattr(config, "OFFSITE_PUSH_ENABLED", "1")

    plain = tmp_path / "not_a_repo2"
    plain.mkdir()
    with pytest.raises(RuntimeError, match="no git repo"):
        offsite.push_corpus(str(plain))


def test_unreachable_remote_raises(corpus, monkeypatch):
    """A dead remote must surface as a task failure, not a silent success."""
    import offsite

    corpus["repo"].delete_remote("origin")
    corpus["repo"].create_remote("origin", str(corpus["work"]) + "-does-not-exist")

    with pytest.raises(Exception):
        offsite.push_corpus(corpus["work"])


# ---------------------------------------------------------------------------
# push_snapshot
# ---------------------------------------------------------------------------

def _make_backup_tree(tmp_path, monkeypatch, names_and_ages):
    import local_db
    sqlite_path = tmp_path / "db" / "sqlite" / "pma.db"
    monkeypatch.setattr(local_db, "db_path", lambda: str(sqlite_path))
    backup_dir = tmp_path / "db" / "backups"
    backup_dir.mkdir(parents=True)
    for name, days_old, content in names_and_ages:
        f = backup_dir / name
        f.write_text(content)
        t = (datetime.now() - timedelta(days=days_old)).timestamp()
        os.utime(f, (t, t))
    return backup_dir


def test_push_snapshot_skipped_when_unconfigured(tmp_path, monkeypatch):
    import offsite
    result = offsite.push_snapshot()
    assert result["copied"] is False
    assert "not configured" in result["reason"]


def test_push_snapshot_gzips_newest_backup(tmp_path, monkeypatch):
    import config
    import offsite

    _make_backup_tree(tmp_path, monkeypatch, [
        ("pma-20260101-0000.db", 5, "old contents"),
        ("pma-20260105-0000.db", 1, "newest contents"),
    ])
    dest_dir = tmp_path / "offsite"
    monkeypatch.setattr(config, "OFFSITE_SNAPSHOT_DIR", str(dest_dir))

    result = offsite.push_snapshot()

    assert result["copied"] is True
    written = dest_dir / "pma-20260105-0000.db.gz"
    assert written.exists()
    with gzip.open(written, "rb") as f:
        assert f.read() == b"newest contents"


def test_push_snapshot_prunes_old_snapshots(tmp_path, monkeypatch):
    import config
    import offsite

    _make_backup_tree(tmp_path, monkeypatch, [("pma-20260105-0000.db", 0, "x")])
    dest_dir = tmp_path / "offsite"
    dest_dir.mkdir()

    stale = dest_dir / "pma-20250101-0000.db.gz"
    stale.write_bytes(b"stale")
    t = (datetime.now() - timedelta(days=90)).timestamp()
    os.utime(stale, (t, t))

    unrelated = dest_dir / "notes.txt"
    unrelated.write_text("keep me")
    os.utime(unrelated, (t, t))

    monkeypatch.setattr(config, "OFFSITE_SNAPSHOT_DIR", str(dest_dir))
    result = offsite.push_snapshot()

    assert result["pruned"] == 1
    assert not stale.exists()
    assert unrelated.exists()      # wrong prefix/suffix — never touched


def test_push_snapshot_raises_when_no_local_backup_exists(tmp_path, monkeypatch):
    """db_backup runs 15 min earlier; an empty dir means it has never worked."""
    import config
    import offsite

    _make_backup_tree(tmp_path, monkeypatch, [])
    monkeypatch.setattr(config, "OFFSITE_SNAPSHOT_DIR", str(tmp_path / "offsite"))

    with pytest.raises(RuntimeError, match="no SQLite backup"):
        offsite.push_snapshot()


# ---------------------------------------------------------------------------
# run_offsite_push — independent targets, aggregated failure
# ---------------------------------------------------------------------------

def test_run_offsite_push_runs_both_targets(corpus, tmp_path, monkeypatch):
    import config
    import offsite

    _make_backup_tree(tmp_path, monkeypatch, [("pma-20260105-0000.db", 0, "data")])
    monkeypatch.setattr(config, "OFFSITE_SNAPSHOT_DIR", str(tmp_path / "offsite"))

    result = offsite.run_offsite_push(corpus["work"])

    assert result["corpus"]["pushed"] is True
    assert result["snapshot"]["copied"] is True


def test_run_offsite_push_still_copies_snapshot_when_push_fails(corpus, tmp_path, monkeypatch):
    """One broken target must not stop the other — but the job still fails."""
    import config
    import offsite

    corpus["repo"].delete_remote("origin")
    corpus["repo"].create_remote("origin", str(tmp_path / "gone.git"))

    _make_backup_tree(tmp_path, monkeypatch, [("pma-20260105-0000.db", 0, "data")])
    dest_dir = tmp_path / "offsite"
    monkeypatch.setattr(config, "OFFSITE_SNAPSHOT_DIR", str(dest_dir))

    with pytest.raises(RuntimeError, match="offsite push failed"):
        offsite.run_offsite_push(corpus["work"])

    assert (dest_dir / "pma-20260105-0000.db.gz").exists()


def test_run_offsite_push_clean_when_nothing_configured(no_remote_corpus):
    """Default install: no remote, no snapshot dir. Must be a quiet no-op."""
    import offsite

    result = offsite.run_offsite_push(no_remote_corpus)
    assert result["corpus"]["pushed"] is False
    assert result["snapshot"]["copied"] is False


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------

def test_offsite_push_handler_is_registered():
    import task_handlers
    assert "offsite_push" in task_handlers.HANDLERS


def test_offsite_push_is_scheduled_after_db_backup():
    import scheduled_tasks

    by_type = {e["task_type"]: e for e in scheduled_tasks.SCHEDULED_TASKS}
    assert "offsite_push" in by_type

    backup = by_type["db_backup"]["trigger_kwargs"]
    push = by_type["offsite_push"]["trigger_kwargs"]
    assert (push["hour"], push["minute"]) > (backup["hour"], backup["minute"])
