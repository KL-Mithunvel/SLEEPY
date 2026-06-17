"""
Dispatch table: task_type → handler(payload, conn).
Handlers are plain functions. They must NOT commit — the worker owns the transaction.
"""

import logging
import sqlite3

import ai_client
import md_indexer

logger = logging.getLogger(__name__)


def _handle_md_reindex(payload: dict, conn: sqlite3.Connection):
    """Full incremental re-index of the MD corpus into ChromaDB."""
    logger.info("md_reindex started: %s", payload)
    count = md_indexer.index_all(conn)
    logger.info("md_reindex done: %d chunks indexed", count)


def _handle_morning_briefing(payload: dict, conn: sqlite3.Connection):
    """Generate the morning briefing and log it to ai_events."""
    logger.info("morning_briefing started")
    briefing = ai_client.generate_morning_briefing(conn)
    logger.info("morning_briefing done (%d chars)", len(briefing))


HANDLERS: dict[str, callable] = {
    "md_reindex": _handle_md_reindex,
    "morning_briefing": _handle_morning_briefing,
}


def dispatch(task_type: str, payload: dict, conn: sqlite3.Connection):
    handler = HANDLERS.get(task_type)
    if handler is None:
        raise ValueError(f"Unknown task_type: {task_type!r}")
    handler(payload, conn)
