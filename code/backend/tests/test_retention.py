"""
Tests for retention.py — archive old alert/metric rows, push, THEN delete.

The property under test is the ordering, not the rollup arithmetic: rows may
only disappear once their rollup is in a commit that reached the remote. The
push tests drive real git against a real bare repo, as test_offsite.py does,
because a mocked push would pass just as happily with the wrong arguments.
"""

import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

TODAY = date(2026, 10, 3)   # alerts cutoff 2026-09-03, metrics cutoff 2026-07-05


@pytest.fixture()
def conn(app):
    import local_db
    c = local_db.get_db()
    c.execute("DELETE FROM system_alerts")
    c.execute("DELETE FROM system_metrics")
    c.commit()
    yield c
    c.rollback()
    local_db.return_db(c)


@pytest.fixture(autouse=True)
def defaults(monkeypatch):
    import config
    monkeypatch.setattr(config, "ALERT_RETENTION_DAYS", 30)
    monkeypatch.setattr(config, "METRICS_RETENTION_DAYS", 90)
    monkeypatch.setattr(config, "OFFSITE_PUSH_ENABLED", "auto")
    monkeypatch.setattr(config, "CORPUS_GIT_REMOTE", "origin")
    monkeypatch.setattr(config, "CORPUS_GIT_BRANCH", "")
    monkeypatch.setattr(config, "OFFSITE_SSH_KEY_PATH", "")


def _init_repo(path):
    import git
    path.mkdir()
    repo = git.Repo.init(path, initial_branch="main")
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test")
        cw.set_value("user", "email", "test@example.invalid")
    (path / "ABOUT.md").write_text("# corpus\n")
    repo.index.add(["ABOUT.md"])
    repo.index.commit("initial")
    return repo


@pytest.fixture()
def corpus(tmp_path):
    """A corpus repo with a real bare remote named 'origin'."""
    import git
    remote = tmp_path / "remote.git"
    git.Repo.init(remote, bare=True)
    repo = _init_repo(tmp_path / "corpus")
    repo.create_remote("origin", str(remote))
    return {"work": str(tmp_path / "corpus"), "remote": str(remote), "repo": repo}


@pytest.fixture()
def no_remote_corpus(tmp_path):
    _init_repo(tmp_path / "solo")
    return str(tmp_path / "solo")


def _alert(conn, key, when, emailed=0, suppressed=0):
    conn.execute(
        "INSERT INTO system_alerts (alert_key, subject, body, suppressed, emailed, created_at) "
        "VALUES (?, ?, 'b', ?, ?, ?)",
        (key, f"subj {key}", suppressed, emailed, when),
    )


def _metric(conn, when, pct=50.0, free=1000, mem=500):
    conn.execute(
        "INSERT INTO system_metrics (disk_used_pct, disk_free_bytes, mem_available_bytes, "
        "corpus_bytes, db_bytes, backups_bytes, created_at) VALUES (?, ?, ?, 1, 2, 3, ?)",
        (pct, free, mem, when),
    )


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _archive(root, name):
    with open(os.path.join(root, "archive", "ops", name), encoding="utf-8") as f:
        return json.load(f)


def _remote_has(remote, relpath):
    import git
    tree = git.Repo(remote).commit("main").tree
    return relpath in {b.path for b in tree.traverse()}


# ---------------------------------------------------------------------------
# The ordering guarantee
# ---------------------------------------------------------------------------

def test_rows_are_deleted_only_after_the_push_reached_the_remote(conn, corpus):
    import retention

    for _ in range(5):
        _alert(conn, "failed_tasks:manual", "2026-08-10 10:00:00")
    conn.commit()

    result = retention.run_retention(conn, corpus["work"], today=TODAY)

    assert result["pushed"] is True, result
    assert result["deleted_alerts"] == 5
    assert _count(conn, "system_alerts") == 0
    assert _remote_has(corpus["remote"], "archive/ops/alerts-2026-08.json")


def test_no_remote_means_nothing_is_deleted(conn, no_remote_corpus):
    """An unpushed rollup is not a backup — keep the raw rows."""
    import retention

    _alert(conn, "disk_space:low", "2026-08-10 10:00:00")
    conn.commit()

    result = retention.run_retention(conn, no_remote_corpus, today=TODAY)

    assert result["archived"] is True
    assert result["pushed"] is False
    assert result["deleted_alerts"] == 0
    assert _count(conn, "system_alerts") == 1
    assert "rows kept" in result["reason"]


def test_a_failing_push_leaves_every_row_in_place(conn, corpus, monkeypatch):
    import config
    import retention

    monkeypatch.setattr(config, "OFFSITE_PUSH_ENABLED", "1")
    corpus["repo"].delete_remote("origin")        # explicitly enabled + no remote = raises
    _alert(conn, "disk_space:low", "2026-08-10 10:00:00")
    _metric(conn, "2026-06-01 10:00:00")
    conn.commit()

    with pytest.raises(RuntimeError):
        retention.run_retention(conn, corpus["work"], today=TODAY)

    assert _count(conn, "system_alerts") == 1
    assert _count(conn, "system_metrics") == 1


def test_no_git_repo_means_nothing_is_deleted(conn, tmp_path):
    import retention

    _alert(conn, "disk_space:low", "2026-08-10 10:00:00")
    conn.commit()
    root = tmp_path / "plain"
    root.mkdir()

    result = retention.run_retention(conn, str(root), today=TODAY)

    assert result["deleted_alerts"] == 0
    assert _count(conn, "system_alerts") == 1


# ---------------------------------------------------------------------------
# What is eligible, and what gets written
# ---------------------------------------------------------------------------

def test_recent_rows_and_the_cutoff_day_are_kept(conn, corpus):
    import retention

    _alert(conn, "a", "2026-09-02 23:59:59")     # before cutoff: archived + deleted
    _alert(conn, "a", "2026-09-03 00:00:01")     # the cutoff day itself: kept
    _alert(conn, "a", "2026-10-01 08:00:00")     # recent: kept
    conn.commit()

    result = retention.run_retention(conn, corpus["work"], today=TODAY)

    assert result["deleted_alerts"] == 1
    assert _count(conn, "system_alerts") == 2


def test_alert_rollup_is_one_entry_per_day_per_key(conn, corpus):
    import retention

    for h in range(4):
        _alert(conn, "failed_tasks:manual", f"2026-08-10 0{h}:00:00", emailed=0, suppressed=1)
    _alert(conn, "disk_space:low", "2026-08-10 12:00:00", emailed=1)
    _alert(conn, "failed_tasks:manual", "2026-08-11 01:00:00")
    conn.commit()

    retention.run_retention(conn, corpus["work"], today=TODAY)

    data = _archive(corpus["work"], "alerts-2026-08.json")
    day = data["days"]["2026-08-10"]
    assert day["failed_tasks:manual"]["count"] == 4
    assert day["failed_tasks:manual"]["suppressed"] == 4
    assert day["failed_tasks:manual"]["first_seen"] == "2026-08-10 00:00:00"
    assert day["failed_tasks:manual"]["last_seen"] == "2026-08-10 03:00:00"
    assert day["disk_space:low"]["emailed"] == 1
    assert data["days"]["2026-08-11"]["failed_tasks:manual"]["count"] == 1
    assert "body" not in day["disk_space:low"]            # free text is not archived


def test_metrics_rollup_keeps_the_worst_case_of_each_day(conn, corpus):
    import retention

    _metric(conn, "2026-06-01 01:00:00", pct=60.0, free=4000, mem=900)
    _metric(conn, "2026-06-01 13:00:00", pct=71.5, free=2500, mem=300)
    conn.commit()

    retention.run_retention(conn, corpus["work"], today=TODAY)

    day = _archive(corpus["work"], "metrics-2026-06.json")["days"]["2026-06-01"]
    assert day["samples"] == 2
    assert day["disk_used_pct_max"] == 71.5
    assert day["disk_used_pct_min"] == 60.0
    assert day["disk_free_bytes_min"] == 2500
    assert day["mem_available_bytes_min"] == 300
    assert _count(conn, "system_metrics") == 0


def test_metrics_inside_their_longer_window_are_untouched(conn, corpus):
    """The Admin > Server chart reads up to 90 days of raw samples."""
    import retention

    _metric(conn, "2026-08-01 10:00:00")          # 63 days old: inside 90
    conn.commit()

    result = retention.run_retention(conn, corpus["work"], today=TODAY)

    assert result["archived"] is False
    assert _count(conn, "system_metrics") == 1


def test_nothing_eligible_is_a_quiet_noop(conn, corpus):
    import retention

    _alert(conn, "a", "2026-10-02 10:00:00")
    conn.commit()
    head_before = corpus["repo"].head.commit.hexsha

    result = retention.run_retention(conn, corpus["work"], today=TODAY)

    assert result["archived"] is False
    assert corpus["repo"].head.commit.hexsha == head_before
    assert not os.path.exists(os.path.join(corpus["work"], "archive"))


# ---------------------------------------------------------------------------
# Idempotence — a crash between archive and delete must not double-count
# ---------------------------------------------------------------------------

def test_rerunning_after_a_crash_does_not_double_count(conn, corpus):
    import retention

    for _ in range(3):
        _alert(conn, "a", "2026-08-10 10:00:00")
    conn.commit()

    # First run archives and pushes, but "crashes" before the worker commits
    # the DELETE: roll the deletion back so the rows reappear.
    retention.run_retention(conn, corpus["work"], today=TODAY)
    conn.rollback()
    assert _count(conn, "system_alerts") == 3

    retention.run_retention(conn, corpus["work"], today=TODAY)

    assert _archive(corpus["work"], "alerts-2026-08.json")["days"]["2026-08-10"]["a"]["count"] == 3
    assert _count(conn, "system_alerts") == 0


def test_a_later_run_adds_new_days_without_touching_old_ones(conn, corpus):
    import retention

    _alert(conn, "a", "2026-08-10 10:00:00")
    conn.commit()
    retention.run_retention(conn, corpus["work"], today=TODAY)
    conn.commit()

    _alert(conn, "a", "2026-08-11 10:00:00")
    conn.commit()
    retention.run_retention(conn, corpus["work"], today=date(2026, 10, 4))

    days = _archive(corpus["work"], "alerts-2026-08.json")["days"]
    assert set(days) == {"2026-08-10", "2026-08-11"}
    assert days["2026-08-10"]["a"]["count"] == 1


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def test_archive_dir_is_skipped_by_ou_discovery(tmp_path):
    """archive/ops must never be mistaken for a life-domain OU."""
    import materialiser
    (tmp_path / "Personal").mkdir()
    (tmp_path / "archive" / "ops").mkdir(parents=True)

    assert materialiser._find_ous(str(tmp_path)) == ["Personal"]


def test_retention_is_registered_scheduled_after_offsite_and_self_healable():
    import scheduled_tasks
    import selfheal
    import task_handlers

    assert "retention" in task_handlers.HANDLERS
    assert "retention" in selfheal._AUTO_RECOVER_TASK_TYPES
    by_type = {e["task_type"]: e for e in scheduled_tasks.SCHEDULED_TASKS}
    push = by_type["offsite_push"]["trigger_kwargs"]
    ret = by_type["retention"]["trigger_kwargs"]
    assert (ret["hour"], ret["minute"]) > (push["hour"], push["minute"])


def test_failed_task_without_completed_at_is_eventually_pruned(conn):
    """Six July failures had completed_at NULL, and NULL < x is never true."""
    import task_handlers

    conn.execute("DELETE FROM task_queue")
    conn.execute(
        "INSERT INTO task_queue (task_type, payload, status, attempts, max_attempts, created_at) "
        "VALUES ('email', '{}', 'failed', 3, 3, datetime('now', '-40 days', 'localtime'))"
    )
    conn.execute(
        "INSERT INTO task_queue (task_type, payload, status, attempts, max_attempts, created_at) "
        "VALUES ('email', '{}', 'failed', 3, 3, datetime('now', '-1 days', 'localtime'))"
    )
    conn.commit()

    import housekeeping
    from unittest import mock
    with mock.patch.object(housekeeping, "run_housekeeping", return_value={
            "findings_added_to_inbox": 0, "archive": {"moved": 0}}):
        task_handlers._handle_housekeeping({}, conn)

    remaining = conn.execute("SELECT COUNT(*) FROM task_queue WHERE task_type='email'").fetchone()[0]
    assert remaining == 1
