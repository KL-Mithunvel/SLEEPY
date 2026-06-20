"""
Separate worker process: APScheduler + task_queue drainer.
Start with: uv run python worker.py  (from repo root via bat wrapper)
Never run inside the web process.
"""

import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler

import local_db
import task_handlers
import task_queue
import scheduled_tasks as sched_registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")

DRAIN_INTERVAL = 5  # seconds between drain loops


def _enqueue_scheduled(task_type: str, payload: dict):
    conn = local_db.get_db()
    try:
        tid = task_queue.enqueue(conn, task_type, payload)
        logger.info("Scheduled task enqueued: %s id=%d", task_type, tid)
    finally:
        local_db.return_db(conn)


def _drain_once():
    conn = local_db.get_db()
    try:
        while True:
            task = task_queue.claim_next(conn)
            if task is None:
                break
            logger.info("Running task id=%d type=%s", task["id"], task["task_type"])
            try:
                task_handlers.dispatch(task["task_type"], task["payload"], conn)
                task_queue.mark_done(conn, task["id"])
                logger.info("Task id=%d done", task["id"])
            except Exception as exc:
                logger.exception("Task id=%d failed: %s", task["id"], exc)
                task_queue.mark_failed(conn, task["id"], str(exc))
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

    scheduler = BackgroundScheduler()
    count = _register_jobs(scheduler)
    scheduler.start()
    logger.info("Scheduler started with %d jobs", count)

    try:
        while True:
            _drain_once()
            time.sleep(DRAIN_INTERVAL)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
