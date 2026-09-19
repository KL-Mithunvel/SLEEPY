"""
Tests for selfheal.py — the every-15-minutes detect-and-repair job.

The important property under test is not just "does it fix things" but where
the line sits: idempotent, reversible repairs happen automatically; anything
that could duplicate a side effect or destroy data is only ever reported.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")


@pytest.fixture()
def conn(app):
    import local_db
    c = local_db.get_db()
    c.execute("DELETE FROM task_queue")
    c.execute("DELETE FROM system_alerts")
    c.commit()
    yield c
    c.rollback()
    local_db.return_db(c)


@pytest.fixture()
def corpus(tmp_path):
    """Minimal corpus: one OU with today's Daily file already materialised."""
    from datetime import date
    root = tmp_path / "klm"
    (root / "Personal" / "Daily").mkdir(parents=True)
    (root / "Personal" / "Daily" / f"{date.today().isoformat()}.md").write_text("## Tasks\n")
    (root / "Personal" / "project.md").write_text("# a project\n")
    return str(root)


@pytest.fixture(autouse=True)
def quiet_alerts(monkeypatch):
    import config
    monkeypatch.setattr(config, "ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "ALERT_EMAIL", "owner@example.invalid")
    monkeypatch.setattr(config, "ALERT_COOLDOWN_HOURS", 6)
    monkeypatch.setattr(config, "DISK_ALERT_PERCENT", 85)
    monkeypatch.setattr(config, "HEALTH_CHECK_URL", "")   # off unless a test wants it


def _status(findings, name):
    return next(f.status for f in findings if f.check == name)


# ---------------------------------------------------------------------------
# Stale locks
# ---------------------------------------------------------------------------

def test_stale_lock_is_cleared(conn, corpus):
    import md_editor
    import selfheal

    git_dir = os.path.join(corpus, ".git")
    os.makedirs(git_dir)
    lock = os.path.join(git_dir, "index.lock")
    open(lock, "w").close()
    old = time.time() - (md_editor._GIT_LOCK_STALE_SEC + 60)
    os.utime(lock, (old, old))

    finding = selfheal.check_stale_locks(conn, corpus)

    assert finding.status == selfheal.FIXED
    assert not os.path.exists(lock)


def test_no_locks_is_ok(conn, corpus):
    import selfheal
    assert selfheal.check_stale_locks(conn, corpus).status == selfheal.OK


# ---------------------------------------------------------------------------
# Failed task recovery
# ---------------------------------------------------------------------------

def _fail_task(conn, task_type):
    import task_queue
    tid = task_queue.enqueue(conn, task_type, {})
    conn.execute("UPDATE task_queue SET status = 'failed', attempts = 3 WHERE id = ?", (tid,))
    conn.commit()
    return tid


def test_idempotent_failed_task_is_requeued(conn, corpus):
    import selfheal
    import task_queue

    tid = _fail_task(conn, "materialise")

    finding = selfheal.check_failed_tasks(conn, corpus)

    assert finding.status == selfheal.FIXED
    row = task_queue.get(conn, tid)
    assert row["status"] == "pending"
    assert row["attempts"] == 0
    assert row["recovered_at"] is not None


def test_task_with_side_effects_is_never_auto_retried(conn, corpus):
    """Re-running a failed email could send a message that already went out."""
    import selfheal
    import task_queue

    tid = _fail_task(conn, "email")

    finding = selfheal.check_failed_tasks(conn, corpus)

    assert finding.status == selfheal.ALERT
    assert task_queue.get(conn, tid)["status"] == "failed"


def test_morning_briefing_is_never_auto_retried(conn, corpus):
    """It sends mail — a failure after the send would duplicate the briefing."""
    import selfheal
    import task_queue

    tid = _fail_task(conn, "morning_briefing")
    selfheal.check_failed_tasks(conn, corpus)
    assert task_queue.get(conn, tid)["status"] == "failed"


def test_a_task_is_only_recovered_once(conn, corpus):
    """One more chance, not an infinite retry loop."""
    import selfheal
    import task_queue

    tid = _fail_task(conn, "materialise")
    selfheal.check_failed_tasks(conn, corpus)

    # It fails again after its recovery run.
    conn.execute("UPDATE task_queue SET status = 'failed' WHERE id = ?", (tid,))
    conn.commit()

    finding = selfheal.check_failed_tasks(conn, corpus)

    assert finding.status == selfheal.OK
    assert task_queue.get(conn, tid)["status"] == "failed"   # left for a human


def test_no_failed_tasks_is_ok(conn, corpus):
    import selfheal
    assert selfheal.check_failed_tasks(conn, corpus).status == selfheal.OK


# ---------------------------------------------------------------------------
# Today's Daily file
# ---------------------------------------------------------------------------

def test_missing_daily_file_enqueues_materialise(conn, tmp_path):
    import selfheal

    root = tmp_path / "klm2"
    (root / "Personal" / "Daily").mkdir(parents=True)   # no file for today

    finding = selfheal.check_todays_daily(conn, str(root))

    assert finding.status == selfheal.FIXED
    queued = conn.execute(
        "SELECT task_type FROM task_queue WHERE status = 'pending'"
    ).fetchall()
    assert [r["task_type"] for r in queued] == ["materialise"]


def test_present_daily_file_is_ok(conn, corpus):
    import selfheal
    assert selfheal.check_todays_daily(conn, corpus).status == selfheal.OK


def test_does_not_pile_up_duplicate_materialise_tasks(conn, tmp_path):
    import selfheal
    import task_queue

    root = tmp_path / "klm3"
    (root / "Personal" / "Daily").mkdir(parents=True)
    task_queue.enqueue(conn, "materialise", {})

    finding = selfheal.check_todays_daily(conn, str(root))

    assert finding.status == selfheal.OK
    count = conn.execute(
        "SELECT COUNT(*) c FROM task_queue WHERE task_type = 'materialise'"
    ).fetchone()["c"]
    assert count == 1


def test_empty_corpus_is_ok(conn, tmp_path):
    import selfheal
    empty = tmp_path / "empty"
    empty.mkdir()
    assert selfheal.check_todays_daily(conn, str(empty)).status == selfheal.OK


# ---------------------------------------------------------------------------
# Vector index
# ---------------------------------------------------------------------------

def test_empty_index_with_files_enqueues_reindex(conn, corpus, monkeypatch):
    import md_indexer
    import selfheal

    class _EmptyCollection:
        def count(self):
            return 0

    monkeypatch.setattr(md_indexer, "_get_collection", lambda: _EmptyCollection())

    finding = selfheal.check_vector_index(conn, corpus)

    assert finding.status == selfheal.FIXED
    queued = conn.execute(
        "SELECT task_type FROM task_queue WHERE status = 'pending'"
    ).fetchall()
    assert [r["task_type"] for r in queued] == ["md_reindex"]


def test_populated_index_is_ok(conn, corpus, monkeypatch):
    import md_indexer
    import selfheal

    class _FullCollection:
        def count(self):
            return 42

    monkeypatch.setattr(md_indexer, "_get_collection", lambda: _FullCollection())
    assert selfheal.check_vector_index(conn, corpus).status == selfheal.OK


def test_unreachable_chroma_alerts_rather_than_rebuilding(conn, corpus, monkeypatch):
    import md_indexer
    import selfheal

    def _boom():
        raise ConnectionError("connection refused")

    monkeypatch.setattr(md_indexer, "_get_collection", _boom)

    finding = selfheal.check_vector_index(conn, corpus)

    assert finding.status == selfheal.ALERT
    assert conn.execute("SELECT COUNT(*) c FROM task_queue").fetchone()["c"] == 0


def test_corpus_with_no_md_files_is_ok(conn, tmp_path):
    import selfheal
    empty = tmp_path / "nofiles"
    empty.mkdir()
    assert selfheal.check_vector_index(conn, str(empty)).status == selfheal.OK


# ---------------------------------------------------------------------------
# Disk, DB integrity, backend health
# ---------------------------------------------------------------------------

def test_disk_space_ok_below_threshold(conn, corpus, monkeypatch):
    import selfheal
    import shutil as sh

    monkeypatch.setattr(sh, "disk_usage", lambda p: sh._ntuple_diskusage(100, 50, 50))
    assert selfheal.check_disk_space(conn, corpus).status == selfheal.OK


def test_disk_space_alerts_above_threshold(conn, corpus, monkeypatch):
    import selfheal
    import shutil as sh

    monkeypatch.setattr(sh, "disk_usage", lambda p: sh._ntuple_diskusage(100, 92, 8))
    finding = selfheal.check_disk_space(conn, corpus)
    assert finding.status == selfheal.ALERT
    assert "92%" in finding.detail


def test_db_integrity_ok(conn, corpus):
    import selfheal
    assert selfheal.check_db_integrity(conn, corpus).status == selfheal.OK


def test_backend_health_skipped_when_unconfigured(conn, corpus):
    import selfheal
    assert selfheal.check_backend_health(conn, corpus).status == selfheal.OK


def test_backend_health_alerts_when_unreachable(conn, corpus, monkeypatch):
    import config
    import selfheal

    monkeypatch.setattr(config, "HEALTH_CHECK_URL", "http://127.0.0.1:1/healthz")
    monkeypatch.setattr(selfheal, "_HEALTH_ATTEMPTS", 1)
    monkeypatch.setattr(selfheal, "_HEALTH_TIMEOUT_SEC", 1)

    finding = selfheal.check_backend_health(conn, corpus)
    assert finding.status == selfheal.ALERT
    assert "restart backend" in finding.detail


# ---------------------------------------------------------------------------
# run_checks()
# ---------------------------------------------------------------------------

def test_run_checks_returns_a_finding_per_check(conn, corpus, monkeypatch):
    import md_indexer
    import selfheal

    monkeypatch.setattr(md_indexer, "_get_collection", lambda: type("C", (), {"count": lambda s: 1})())

    findings = selfheal.run_checks(conn, corpus)
    assert len(findings) == len(selfheal._CHECKS)
    assert {f.check for f in findings} >= {"stale_locks", "disk_space", "db_integrity"}


def test_run_checks_raises_alerts_for_alert_findings(conn, corpus, monkeypatch):
    import md_indexer
    import selfheal

    def _boom():
        raise ConnectionError("chroma down")

    monkeypatch.setattr(md_indexer, "_get_collection", _boom)

    selfheal.run_checks(conn, corpus)

    keys = [r["alert_key"] for r in conn.execute("SELECT alert_key FROM system_alerts")]
    assert "vector_index:unreachable" in keys


def test_one_broken_check_does_not_stop_the_others(conn, corpus, monkeypatch):
    """A single bad probe must not take the whole safety net down."""
    import selfheal

    def _explode(conn_, root):
        raise RuntimeError("probe is broken")

    monkeypatch.setattr(selfheal, "_CHECKS", (_explode, selfheal.check_disk_space))

    findings = selfheal.run_checks(conn, corpus)

    assert len(findings) == 2
    assert _status(findings, "_explode") == selfheal.ALERT
    assert _status(findings, "disk_space") in (selfheal.OK, selfheal.ALERT)


def test_run_checks_does_not_commit(conn, corpus, monkeypatch):
    """Runs inside a handler — the worker owns the transaction."""
    import md_indexer
    import selfheal

    monkeypatch.setattr(md_indexer, "_get_collection", lambda: type("C", (), {"count": lambda s: 1})())
    _fail_task(conn, "materialise")   # commits, so the rollback below is clean

    selfheal.run_checks(conn, corpus)
    conn.rollback()

    assert conn.execute("SELECT COUNT(*) c FROM system_alerts").fetchone()["c"] == 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_self_check_handler_is_registered():
    import task_handlers
    assert "self_check" in task_handlers.HANDLERS


def test_self_check_is_scheduled():
    import scheduled_tasks
    by_type = {e["task_type"]: e for e in scheduled_tasks.SCHEDULED_TASKS}
    assert by_type["self_check"]["trigger"] == "interval"
    assert by_type["self_check"]["trigger_kwargs"]["seconds"] > 0
