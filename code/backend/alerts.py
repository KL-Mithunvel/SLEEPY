"""
Operational alerting — turns silent internal failures into something the owner
actually sees.

Before this module, a background task that burned through `max_attempts` set
`status='failed'` in SQLite and stopped there: no email, no inbox finding, no
UI surface. A broken `materialise` meant Today quietly stayed empty until
somebody noticed by eye. This is the "Layer 6 — Monitoring" task-failure alert
from docs/SECURITY_REVIEW.md.

Design notes:

* Alerts are **enqueued as `email` tasks**, never sent inline. The worker's
  drain loop must not block on a Graph API round-trip, and going through the
  queue means alert delivery inherits the same retry/backoff as everything
  else.
* `notify()` never commits. Called from inside a handler, the worker's
  `mark_done` commits for it (handlers must not commit — see CLAUDE.md); called
  from the worker's failure path, the worker commits explicitly.
* Every alert is recorded in `system_alerts` whether or not it was emailed, so
  a throttled flood still leaves a full trail.
* `email` task failures never alert — that is the one loop this must not close.

Public API:
    notify(conn, alert_key, subject, body, *, cooldown_hours=None) -> bool
    task_failed(conn, task) -> bool
"""

import logging
from datetime import datetime, timedelta

import config
import task_queue

logger = logging.getLogger(__name__)

# Task types that must never generate an alert email, because the alert itself
# is delivered by one: a failing mail path would otherwise enqueue an alert
# about the failure, which fails, which enqueues another.
_NO_ALERT_TASK_TYPES = frozenset({"email"})


def _recent_unsuppressed(conn, alert_key: str, cooldown_hours: int):
    """Most recent actually-delivered alert for this key inside the window."""
    cutoff = (datetime.now() - timedelta(hours=cooldown_hours)).strftime("%Y-%m-%d %H:%M:%S")
    return conn.execute(
        """
        SELECT id, created_at FROM system_alerts
        WHERE alert_key = ? AND suppressed = 0 AND created_at >= ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (alert_key, cutoff),
    ).fetchone()


def notify(conn, alert_key: str, subject: str, body: str, *, cooldown_hours: int | None = None) -> bool:
    """
    Record an alert and, unless throttled, enqueue an email to the owner.

    `alert_key` is the throttling identity — reuse the same key for repeats of
    the same condition (e.g. "task_failed:materialise"), so a job failing every
    15 minutes produces one mail per cooldown window, not ninety-six a day.

    Returns True if an email was enqueued, False if it was recorded only
    (throttled, alerting disabled, or no destination address configured).
    Never raises: an alert that blows up must not take down the thing that was
    trying to report a problem.
    """
    if cooldown_hours is None:
        cooldown_hours = config.ALERT_COOLDOWN_HOURS

    try:
        if not config.ALERTS_ENABLED:
            logger.info("alerts: disabled, not sending %r", alert_key)
            _record(conn, alert_key, subject, body, suppressed=1, emailed=0)
            return False

        if _recent_unsuppressed(conn, alert_key, cooldown_hours) is not None:
            logger.info("alerts: %r throttled (cooldown %dh)", alert_key, cooldown_hours)
            _record(conn, alert_key, subject, body, suppressed=1, emailed=0)
            return False

        to = config.ALERT_EMAIL
        if not to:
            logger.warning("alerts: %r raised but no ALERT_EMAIL/USER_EMAIL configured", alert_key)
            _record(conn, alert_key, subject, body, suppressed=0, emailed=0)
            return False

        _record(conn, alert_key, subject, body, suppressed=0, emailed=1)
        task_queue.enqueue(
            conn, "email", {"to": to, "subject": subject, "body": body}, commit=False
        )
        logger.warning("alerts: %r raised — email queued to %s", alert_key, to)
        return True
    except Exception:
        logger.exception("alerts: failed to raise %r", alert_key)
        return False


def _record(conn, alert_key: str, subject: str, body: str, *, suppressed: int, emailed: int) -> None:
    conn.execute(
        """
        INSERT INTO system_alerts (alert_key, subject, body, suppressed, emailed)
        VALUES (?, ?, ?, ?, ?)
        """,
        (alert_key, subject[:500], (body or "")[:4000], suppressed, emailed),
    )


# ---------------------------------------------------------------------------
# Specific alert shapes
# ---------------------------------------------------------------------------

def task_failed(conn, task: dict) -> bool:
    """
    Alert on a task that has exhausted its retries. Call only once the row has
    actually reached status='failed' — retries in flight are not news.
    """
    task_type = task.get("task_type", "?")
    if task_type in _NO_ALERT_TASK_TYPES:
        logger.error(
            "alerts: task id=%s type=%s failed permanently — not emailing (would loop): %s",
            task.get("id"), task_type, task.get("last_error"),
        )
        return False

    subject = f"[SLEEPY] Background task failed: {task_type}"
    body = "\n".join([
        f"Task {task.get('id')} ({task_type}) failed permanently and will not retry.",
        "",
        f"Attempts:   {task.get('attempts')}/{task.get('max_attempts')}",
        f"Created:    {task.get('created_at')}",
        f"Last error: {task.get('last_error')}",
        "",
        "The job is now status='failed' in task_queue. The self-check job will",
        "requeue it once automatically if it is an idempotent type; if this mail",
        "repeats, the underlying cause needs a look.",
    ])
    return notify(conn, f"task_failed:{task_type}", subject, body)
