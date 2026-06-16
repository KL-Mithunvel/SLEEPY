"""
Separate worker process: APScheduler + task_queue drainer.
Start with: uv run python worker.py
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


def main():
    local_db.init_db()
    logger.info("Worker started")

    scheduler = BackgroundScheduler()
    for entry in sched_registry.SCHEDULED_TASKS:
        scheduler.add_job(
            _enqueue_scheduled,
            "cron",
            kwargs={"task_type": entry["task_type"], "payload": entry["payload"]},
            **entry["cron"],
        )
    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(sched_registry.SCHEDULED_TASKS))

    try:
        while True:
            _drain_once()
            time.sleep(DRAIN_INTERVAL)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
