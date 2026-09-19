"""
Separate worker process: APScheduler + task_queue drainer.
Start with: uv run python worker.py  (from repo root via bat wrapper)
Never run inside the web process.
"""

import logging
import os
import signal
import time

from apscheduler.schedulers.background import BackgroundScheduler

import alerts
import config
import local_db
import task_handlers
import task_queue
import scheduled_tasks as sched_registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")

DRAIN_INTERVAL = 5  # seconds between drain loops

# Set by the SIGTERM/SIGINT handler and by the stop-sentinel check. The drain
# loop reads it between tasks so a stop lands on a task boundary rather than
# in the middle of one.
_stopping = False

# id of the task currently being dispatched, so a stop that lands mid-task can
# hand it straight back to the queue instead of leaving it 'running' for the
# full 30-minute lock window.
_current_task_id: int | None = None


def _request_stop(signum=None, frame=None):
    """
    SIGTERM/SIGINT handler. Docker sends SIGTERM on `compose stop|restart`, and
    Python's default handler for it terminates the process outright — no
    exception, so neither the `finally` blocks nor scheduler.shutdown() ever
    ran, and any in-flight task was abandoned. Raising SystemExit puts the
    shutdown through the same path as Ctrl-C.
    """
    global _stopping
    _stopping = True
    logger.info("Shutdown requested (signal=%s)", signum)
    raise SystemExit(0)


def _stop_requested() -> bool:
    """
    True once a stop has been signalled, or the cooperative stop sentinel
    exists. The sentinel is what makes a clean stop possible on Windows at
    all: Popen.terminate() there is a hard TerminateProcess, so no signal
    handler in this process would ever run.
    """
    if _stopping:
        return True
    try:
        return os.path.exists(config.STOP_SENTINEL_PATH)
    except Exception:
        return False


def _enqueue_scheduled(task_type: str, payload: dict):
    conn = local_db.get_db()
    try:
        tid = task_queue.enqueue(conn, task_type, payload)
        logger.info("Scheduled task enqueued: %s id=%d", task_type, tid)
    finally:
        local_db.return_db(conn)


def _alert_if_permanently_failed(conn, task_id: int):
    """
    mark_failed() only sets status='failed' once attempts >= max_attempts;
    anything below that is a retry still in flight and not worth an email.
    Re-read the row rather than inferring, so the alert condition is exactly
    the queue's own terminal state.
    """
    try:
        row = task_queue.get(conn, task_id)
        if row and row.get("status") == "failed":
            alerts.task_failed(conn, row)
            conn.commit()   # outside a handler here — the worker owns this one
    except Exception:
        logger.exception("Failure alert for task id=%s could not be raised", task_id)


def _drain_once():
    global _current_task_id
    conn = local_db.get_db()
    try:
        while True:
            task = task_queue.claim_next(conn)
            if task is None:
                break
            logger.info("Running task id=%d type=%s", task["id"], task["task_type"])
            _current_task_id = task["id"]
            try:
                task_handlers.dispatch(task["task_type"], task["payload"], conn)
                task_queue.mark_done(conn, task["id"])
                logger.info("Task id=%d done", task["id"])
            except Exception as exc:
                logger.exception("Task id=%d failed: %s", task["id"], exc)
                # Handlers never commit (the worker owns the transaction), so
                # anything a failed handler wrote is still uncommitted here —
                # drop it rather than letting mark_failed's commit sweep in a
                # half-finished DB state.
                try:
                    conn.rollback()
                except Exception:
                    logger.exception("Task id=%d rollback failed", task["id"])
                task_queue.mark_failed(conn, task["id"], str(exc))
                _alert_if_permanently_failed(conn, task["id"])

            # Deliberately NOT a `finally`: SystemExit from the SIGTERM handler
            # can land anywhere inside dispatch(), and it must leave
            # _current_task_id set so the shutdown path can hand that task back
            # to the queue. Clearing it here means only a task that actually
            # reached a conclusion is forgotten.
            _current_task_id = None

            if _stop_requested():
                logger.info("Stop requested — finishing drain at a task boundary")
                break
    finally:
        local_db.return_db(conn)


def _release_in_flight_task():
    """
    Give a half-run task back to the queue on shutdown, so a restart picks it
    up immediately instead of waiting out task_queue.LOCK_MINUTES.
    """
    if _current_task_id is None:
        return
    conn = local_db.get_db()
    try:
        task_queue.release(conn, _current_task_id)
        logger.info("Released in-flight task id=%s back to pending", _current_task_id)
    except Exception:
        logger.exception("Could not release in-flight task id=%s", _current_task_id)
    finally:
        local_db.return_db(conn)


def _register_jobs(scheduler: BackgroundScheduler):
    """Register all enabled jobs from the scheduled_tasks registry."""
    registered = 0
    for entry in sched_registry.SCHEDULED_TASKS:
        if not entry.get("enabled", True):
            logger.info("Skipping disabled job: %s", entry["task_type"])
            continue

        trigger = entry.get("trigger", "cron")
        trigger_kwargs = entry.get("trigger_kwargs") or entry.get("cron", {})

        scheduler.add_job(
            _enqueue_scheduled,
            trigger,
            kwargs={"task_type": entry["task_type"], "payload": entry.get("payload", {})},
            **trigger_kwargs,
        )
        logger.info("Registered %s job: %s %s", trigger, entry["task_type"], trigger_kwargs)
        registered += 1

    return registered


def main():
    local_db.init_db()
    logger.info("Worker started")

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    # A previous run killed mid-commit can leave .git/index.lock behind, and
    # nothing else ever clears it — every corpus write would fail until it was
    # deleted by hand. Startup is the safe moment to sweep it.
    try:
        import md_editor
        md_editor.clear_stale_git_locks(config.USER_DATA_ROOT)
    except Exception:
        logger.exception("Startup lock sweep failed (continuing)")

    scheduler = BackgroundScheduler()
    count = _register_jobs(scheduler)
    scheduler.start()
    logger.info("Scheduler started with %d jobs", count)

    # The 00:05 IST materialise cron only fires if the worker happens to be
    # running at that exact moment — if the machine was off or main.py wasn't
    # started, Active Tasks silently stays empty until the next midnight.
    # Materialise is fully idempotent (see materialiser.py docstring), so it's
    # always safe to also run it once on every startup.
    _enqueue_scheduled("materialise", {})

    try:
        while not _stop_requested():
            _drain_once()
            # Sleep in one-second slices so a stop is noticed within ~1s
            # rather than up to a full drain interval later.
            for _ in range(DRAIN_INTERVAL):
                if _stop_requested():
                    break
                time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        _release_in_flight_task()
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            logger.exception("Scheduler shutdown failed")
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
