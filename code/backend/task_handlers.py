"""
Dispatch table: task_type → handler(payload, conn).
Handlers are plain functions. They must NOT commit — the worker owns the transaction.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


def _handle_md_reindex(payload: dict, conn: sqlite3.Connection):
    """Trigger a full re-index of the user's MD corpus."""
    # Placeholder — wired up when the AI layer is built.
    logger.info("md_reindex triggered: %s", payload)


def _handle_morning_briefing(payload: dict, conn: sqlite3.Connection):
    """Generate and deliver the morning briefing."""
    logger.info("morning_briefing triggered: %s", payload)


HANDLERS: dict[str, callable] = {
    "md_reindex": _handle_md_reindex,
    "morning_briefing": _handle_morning_briefing,
}


def dispatch(task_type: str, payload: dict, conn: sqlite3.Connection):
    handler = HANDLERS.get(task_type)
    if handler is None:
        raise ValueError(f"Unknown task_type: {task_type!r}")
    handler(payload, conn)
