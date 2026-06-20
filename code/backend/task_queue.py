"""
DB-backed task queue (SQLite).
All times stored as ISO-8601 strings in local time (IST).
"""

import json
import sqlite3
from datetime import datetime, timedelta

BACKOFF_BASE_SEC = 30  # delay = 30 * 2^(attempts-1) on failure


def enqueue(conn: sqlite3.Connection, task_type: str, payload: dict, delay_seconds: int = 0) -> int:
    if delay_seconds:
        scheduled_for = (datetime.now() + timedelta(seconds=delay_seconds)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        scheduled_for = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur = conn.execute(
        """
        INSERT INTO task_queue (task_type, payload, scheduled_for)
        VALUES (?, ?, ?)
        """,
        (task_type, json.dumps(payload), scheduled_for),
    )
    conn.commit()
    return cur.lastrowid


def claim_next(conn: sqlite3.Connection) -> dict | None:
    """Claim the next pending task. Returns the task row as a dict or None."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    locked_until = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

    row = conn.execute(
        """
        SELECT * FROM task_queue
        WHERE status = 'pending'
          AND scheduled_for <= ?
          AND attempts < max_attempts
        ORDER BY scheduled_for
        LIMIT 1
        """,
        (now,),
    ).fetchone()

    if row is None:
        return None

    conn.execute(
        """
        UPDATE task_queue
        SET status = 'running', attempts = attempts + 1, locked_until = ?
        WHERE id = ?
        """,
        (locked_until, row["id"]),
    )
    conn.commit()
    task = dict(row)
    task["payload"] = json.loads(task["payload"] or "{}")
    return task


def mark_done(conn: sqlite3.Connection, task_id: int):
    conn.execute(
        """
        UPDATE task_queue
        SET status = 'done', completed_at = datetime('now', 'localtime')
        WHERE id = ?
        """,
        (task_id,),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, task_id: int, error: str, retry_delay_seconds: int | None = None):
    """
    Mark a task as failed and schedule its retry with exponential backoff.

    If retry_delay_seconds is provided it is used directly (backward compat).
    Otherwise the delay is computed as BACKOFF_BASE_SEC * 2^(attempts-1).
    If attempts >= max_attempts the task is set to 'failed' permanently.
    """
    if retry_delay_seconds is None:
        row = conn.execute(
            "SELECT attempts FROM task_queue WHERE id = ?", (task_id,)
        ).fetchone()
        attempts = row["attempts"] if row else 1
        retry_delay_seconds = BACKOFF_BASE_SEC * (2 ** max(0, attempts - 1))

    scheduled_for = (datetime.now() + timedelta(seconds=retry_delay_seconds)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        UPDATE task_queue
        SET status      = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'pending' END,
            last_error  = ?,
            scheduled_for = ?,
            locked_until  = NULL
        WHERE id = ?
        """,
        (error[:2000], scheduled_for, task_id),
    )
    conn.commit()
