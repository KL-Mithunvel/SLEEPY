"""
Tests for the graceful-shutdown path: cooperative stop, in-flight task
release, and the stale git lock sweep.

The failure these prevent is quiet but expensive — a worker hard-killed
mid-task left its row 'running' for a full 30-minute lock window, and a kill
landing mid-commit left .git/index.lock behind, after which every corpus write
failed until someone deleted it by hand.
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
    c.commit()
    yield c
    c.rollback()
    local_db.return_db(c)


@pytest.fixture(autouse=True)
def clean_worker_state(monkeypatch, tmp_path):
    """Worker stop state is module-global — reset it around every test."""
    import config
    import worker
    monkeypatch.setattr(worker, "_stopping", False)
    monkeypatch.setattr(worker, "_current_task_id", None)
    monkeypatch.setattr(config, "STOP_SENTINEL_PATH", str(tmp_path / ".sleepy-stop"))
    yield


# ---------------------------------------------------------------------------
# task_queue.release()
# ---------------------------------------------------------------------------

def test_release_returns_running_task_to_pending(conn):
    import task_queue

    tid = task_queue.enqueue(conn, "materialise", {})
    task_queue.claim_next(conn)
    assert task_queue.get(conn, tid)["status"] == "running"

    task_queue.release(conn, tid)

    row = task_queue.get(conn, tid)
    assert row["status"] == "pending"
    assert row["locked_until"] is None


def test_release_gives_back_the_attempt(conn):
    """Being asked to stop is not a failed attempt — it must not burn one."""
    import task_queue

    tid = task_queue.enqueue(conn, "materialise", {})
    task_queue.claim_next(conn)
    assert task_queue.get(conn, tid)["attempts"] == 1

    task_queue.release(conn, tid)
    assert task_queue.get(conn, tid)["attempts"] == 0


def test_release_leaves_a_finished_task_alone(conn):
    import task_queue

    tid = task_queue.enqueue(conn, "materialise", {})
    task_queue.claim_next(conn)
    task_queue.mark_done(conn, tid)

    task_queue.release(conn, tid)
    assert task_queue.get(conn, tid)["status"] == "done"


def test_release_of_unknown_id_is_a_noop(conn):
    import task_queue
    task_queue.release(conn, 999999)   # must not raise


# ---------------------------------------------------------------------------
# worker stop signalling
# ---------------------------------------------------------------------------

def test_stop_not_requested_by_default():
    import worker
    assert worker._stop_requested() is False


def test_stop_requested_when_sentinel_exists():
    import config
    import worker

    with open(config.STOP_SENTINEL_PATH, "w") as f:
        f.write("stop")
    assert worker._stop_requested() is True


def test_request_stop_raises_system_exit_and_sets_flag():
    """
    Python's default SIGTERM disposition kills the process outright, so no
    finally block or scheduler shutdown ever ran. The handler must turn it
    into the same SystemExit path as Ctrl-C.
    """
    import worker

    with pytest.raises(SystemExit):
        worker._request_stop(15, None)
    assert worker._stopping is True


def test_drain_stops_at_task_boundary_when_sentinel_appears(conn, monkeypatch):
    import config
    import task_handlers
    import task_queue
    import worker

    ran = []

    def _fake_handler(payload, c):
        ran.append(payload.get("n"))
        # Stop requested while the first task is running.
        with open(config.STOP_SENTINEL_PATH, "w") as f:
            f.write("stop")

    monkeypatch.setitem(task_handlers.HANDLERS, "shutdown-test-task", _fake_handler)

    first = task_queue.enqueue(conn, "shutdown-test-task", {"n": 1})
    second = task_queue.enqueue(conn, "shutdown-test-task", {"n": 2})

    worker._drain_once()

    assert ran == [1]                                          # stopped after one
    assert task_queue.get(conn, first)["status"] == "done"     # finished, not abandoned
    assert task_queue.get(conn, second)["status"] == "pending"  # untouched, runs next boot


# ---------------------------------------------------------------------------
# in-flight release on shutdown
# ---------------------------------------------------------------------------

def test_release_in_flight_task_hands_it_back(conn, monkeypatch):
    import task_queue
    import worker

    tid = task_queue.enqueue(conn, "materialise", {})
    task_queue.claim_next(conn)
    monkeypatch.setattr(worker, "_current_task_id", tid)

    worker._release_in_flight_task()

    row = task_queue.get(conn, tid)
    assert row["status"] == "pending"
    assert row["attempts"] == 0


def test_release_in_flight_task_noop_when_nothing_in_flight():
    import worker
    worker._release_in_flight_task()   # must not raise


def test_completed_task_is_not_left_in_flight(conn, monkeypatch):
    """A task that reached a conclusion must not be released a second time."""
    import task_handlers
    import task_queue
    import worker

    monkeypatch.setitem(task_handlers.HANDLERS, "quiet-task", lambda p, c: None)
    task_queue.enqueue(conn, "quiet-task", {})

    worker._drain_once()
    assert worker._current_task_id is None


# ---------------------------------------------------------------------------
# md_editor.clear_stale_git_locks()
# ---------------------------------------------------------------------------

def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_clear_stale_git_locks_removes_abandoned_index_lock(tmp_path):
    """
    Nothing else in the system ever clears .git/index.lock — until it goes,
    every corpus commit fails.
    """
    import md_editor

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    lock = git_dir / "index.lock"
    lock.write_text("")
    _age(lock, md_editor._GIT_LOCK_STALE_SEC + 60)

    removed = md_editor.clear_stale_git_locks(str(tmp_path))

    assert str(lock) in removed
    assert not lock.exists()


def test_clear_stale_git_locks_removes_abandoned_app_lock(tmp_path):
    import md_editor

    lock = tmp_path / md_editor._GIT_LOCK_NAME
    lock.write_text("1234")
    _age(lock, md_editor._GIT_LOCK_STALE_SEC + 60)

    removed = md_editor.clear_stale_git_locks(str(tmp_path))

    assert str(lock) in removed
    assert not lock.exists()


def test_clear_stale_git_locks_leaves_a_live_lock_alone(tmp_path):
    """A commit genuinely in progress must never be unlocked underneath it."""
    import md_editor

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    fresh = git_dir / "index.lock"
    fresh.write_text("")

    app_lock = tmp_path / md_editor._GIT_LOCK_NAME
    app_lock.write_text("1234")

    assert md_editor.clear_stale_git_locks(str(tmp_path)) == []
    assert fresh.exists()
    assert app_lock.exists()


def test_clear_stale_git_locks_handles_missing_locks(tmp_path):
    import md_editor
    assert md_editor.clear_stale_git_locks(str(tmp_path)) == []


def test_clear_stale_git_locks_never_raises_on_bad_root():
    import md_editor
    assert md_editor.clear_stale_git_locks("/nonexistent/path/for/test") == []
