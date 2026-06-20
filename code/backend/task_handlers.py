"""
Dispatch table: task_type → handler(payload, conn).
Handlers are plain functions. They must NOT commit — the worker owns the transaction.
"""

import logging
import sqlite3

import ai_client
import md_indexer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Existing handlers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Phase 5 stubs — implementations added in Phase 7/8/9/11
# ---------------------------------------------------------------------------

def _handle_index_sync(payload: dict, conn: sqlite3.Connection):
    """Incremental ChromaDB sync (changed files only). Full impl in Phase 7."""
    logger.info("index_sync started")
    count = md_indexer.index_all(conn)
    logger.info("index_sync done: %d chunks indexed", count)


def _handle_commit_pending(payload: dict, conn: sqlite3.Connection):
    """Hourly batch git commit of pending user edits. Full impl in Phase 7."""
    logger.info("commit_pending: stub — not yet implemented")


def _handle_housekeeping(payload: dict, conn: sqlite3.Connection):
    """Nightly corpus health checks. Full impl in Phase 8."""
    logger.info("housekeeping: stub — not yet implemented")


def _handle_email(payload: dict, conn: sqlite3.Connection):
    """Send an email via O365 Graph API. Full impl in Phase 11."""
    logger.info("email stub: to=%s subject=%s", payload.get("to"), payload.get("subject"))


def _handle_telegram(payload: dict, conn: sqlite3.Connection):
    """Send a Telegram message. Full impl in Phase 11."""
    logger.info("telegram stub: message=%s", str(payload.get("message", ""))[:80])


def _handle_news_watch_submit(payload: dict, conn: sqlite3.Connection):
    """Submit news watch batch to Anthropic. Full impl in Phase 9."""
    logger.info("news_watch_submit: stub — not yet implemented")


def _handle_news_watch_finalize(payload: dict, conn: sqlite3.Connection):
    """Poll and process news watch batch results. Full impl in Phase 9."""
    logger.info("news_watch_finalize: stub — not yet implemented")


def _handle_jira_create(payload: dict, conn: sqlite3.Connection):
    """Create a Jira issue. Full impl in Phase 11."""
    logger.info("jira_create: stub — project=%s summary=%s", payload.get("project_key"), payload.get("summary"))


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

HANDLERS: dict[str, callable] = {
    # Existing
    "md_reindex":           _handle_md_reindex,
    "morning_briefing":     _handle_morning_briefing,
    # Phase 5 additions
    "index_sync":           _handle_index_sync,
    "commit_pending":       _handle_commit_pending,
    "housekeeping":         _handle_housekeeping,
    "email":                _handle_email,
    "telegram":             _handle_telegram,
    "news_watch_submit":    _handle_news_watch_submit,
    "news_watch_finalize":  _handle_news_watch_finalize,
    "jira_create":          _handle_jira_create,
}


def dispatch(task_type: str, payload: dict, conn: sqlite3.Connection):
    handler = HANDLERS.get(task_type)
    if handler is None:
        raise ValueError(f"Unknown task_type: {task_type!r}")
    handler(payload, conn)
