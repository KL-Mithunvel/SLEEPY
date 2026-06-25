"""
Dispatch table: task_type → handler(payload, conn).
Handlers are plain functions. They must NOT commit — the worker owns the transaction.
"""

import logging
import sqlite3

import ai_client
import config
import md_indexer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core handlers
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
# Phase 7 — Materialiser
# ---------------------------------------------------------------------------

def _handle_materialise(payload: dict, conn: sqlite3.Connection):
    """Nightly materialiser: Recur → Plans + Daily + Govern."""
    import materialiser
    user_nick = payload.get("user_nick") or config.USER_NICK
    result = materialiser.materialise_all(config.USER_DATA_ROOT, user_nick=user_nick)
    logger.info("materialise done: %s", result)

    # Commit newly created/modified files to the data-root git repo
    _commit_data_root(f"materialise: {result['date']}")


def _handle_index_sync(payload: dict, conn: sqlite3.Connection):
    """Incremental ChromaDB sync — only re-indexes changed files."""
    logger.info("index_sync started")
    count = md_indexer.index_all(conn)
    logger.info("index_sync done: %d chunks indexed", count)


def _handle_commit_pending(payload: dict, conn: sqlite3.Connection):
    """Hourly batch git commit of any uncommitted changes in the data root."""
    logger.info("commit_pending started")
    _commit_data_root("batch: auto-commit")
    logger.info("commit_pending done")


def _commit_data_root(message: str) -> None:
    """Stage all changes in USER_DATA_ROOT and commit. No-op if clean."""
    try:
        import git

        data_root = config.USER_DATA_ROOT
        try:
            repo = git.Repo(data_root, search_parent_directories=False)
        except git.InvalidGitRepositoryError:
            logger.info("commit: no git repo at %s, skipping", data_root)
            return

        status = repo.git.status("--porcelain")
        if not status.strip():
            logger.info("commit: repo is clean, nothing to commit")
            return

        repo.git.add(A=True)

        from datetime import datetime, timedelta, timezone
        _IST = timezone(timedelta(hours=5, minutes=30))
        ts = datetime.now(_IST).strftime("%Y-%m-%dT%H:%M IST")
        author = git.Actor("Arivu Baalan", "arivu@smtw.in")
        commit = repo.index.commit(
            f"{message} — {ts}",
            author=author,
            committer=author,
        )
        logger.info("commit: %s (%s)", commit.hexsha[:8], message)
    except Exception:
        logger.exception("commit_data_root failed")


# ---------------------------------------------------------------------------
# Phase 8 — Housekeeping
# ---------------------------------------------------------------------------

def _handle_housekeeping(payload: dict, conn: sqlite3.Connection):
    """Nightly corpus health checks, inbox findings, archive old daily, prune task queue."""
    import housekeeping as hk
    logger.info("housekeeping started")

    # Prune done/failed task_queue entries older than 14 days (SQLite — handler may do this)
    conn.execute(
        """
        DELETE FROM task_queue
        WHERE status IN ('done', 'failed')
          AND completed_at < datetime('now', '-14 days', 'localtime')
        """
    )
    logger.info("housekeeping: pruned old task_queue rows")

    # Corpus checkers → findings → inbox.md + archive old daily files
    user_nick = payload.get("user_nick") or config.USER_NICK
    result = hk.run_housekeeping(config.USER_DATA_ROOT, user_nick=user_nick)
    logger.info("housekeeping corpus done: %s", result)

    # Commit any inbox.md changes and archive moves
    if result["findings_added_to_inbox"] > 0 or result["archive"]["moved"] > 0:
        _commit_data_root("housekeeping: findings + archive")

    # Log result to ai_events for /api/corpus/housekeeping/results
    import json
    conn.execute(
        """
        INSERT INTO ai_events (event_type, prompt_hash, model, diff, voided, created_at)
        VALUES ('housekeeping', '', '', ?, 0, datetime('now','localtime'))
        """,
        (json.dumps(result),),
    )


# ---------------------------------------------------------------------------
# Phase 9 — News Watch
# ---------------------------------------------------------------------------

def _handle_news_watch_submit(payload: dict, conn: sqlite3.Connection):
    """Submit nightly news batch to Anthropic Message Batches API."""
    import news_watch
    result = news_watch.news_watch_submit_for_user(config.USER_DATA_ROOT)
    logger.info("news_watch_submit: %s", result)


def _handle_news_watch_finalize(payload: dict, conn: sqlite3.Connection):
    """Poll Anthropic batch and write surviving bullets to inbox.md."""
    import news_watch
    result = news_watch.news_watch_finalize_for_user(config.USER_DATA_ROOT)
    logger.info("news_watch_finalize: %s", result)


# ---------------------------------------------------------------------------
# Phase 9 — Integrations (Telegram, O365 email, Jira)
# ---------------------------------------------------------------------------

def _handle_email(payload: dict, conn: sqlite3.Connection):
    """Send an email via O365 Graph API (MSAL client credentials)."""
    import integrations
    to = payload.get("to", "")
    subject = payload.get("subject", "(no subject)")
    body = payload.get("body", "")
    body_html = payload.get("body_html")
    if not to:
        raise ValueError("email task missing required 'to' field")
    ok = integrations.send_email(to, subject, body, body_html=body_html)
    if not ok:
        raise RuntimeError(f"send_email failed for to={to!r}")


def _handle_telegram(payload: dict, conn: sqlite3.Connection):
    """Send a Telegram message via Bot API."""
    import integrations
    message = payload.get("message", "")
    chat_id = payload.get("chat_id")
    if not message:
        raise ValueError("telegram task missing required 'message' field")
    ok = integrations.send_telegram(message, chat_id=chat_id)
    if not ok:
        raise RuntimeError("send_telegram failed")


def _handle_jira_create(payload: dict, conn: sqlite3.Connection):
    """Create a Jira Cloud issue."""
    import integrations
    project_key = payload.get("project_key", "")
    summary = payload.get("summary", "")
    description = payload.get("description", "")
    issue_type = payload.get("issue_type", "Task")
    if not project_key or not summary:
        raise ValueError("jira_create task missing 'project_key' or 'summary'")
    key = integrations.jira_create(project_key, summary, description, issue_type)
    if key is None:
        raise RuntimeError(f"jira_create failed for project={project_key!r}")
    logger.info("jira_create: created %s", key)


def _handle_jira_sync(payload: dict, conn: sqlite3.Connection):
    """Sync Jira issue statuses into the MD corpus (close ticked tasks)."""
    import integrations
    user_nick = payload.get("user_nick") or config.USER_NICK
    result = integrations.jira_sync_corpus(config.USER_DATA_ROOT, user_nick=user_nick)
    logger.info("jira_sync done: %s", result)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

HANDLERS: dict[str, callable] = {
    # Core
    "md_reindex":           _handle_md_reindex,
    "morning_briefing":     _handle_morning_briefing,
    # Phase 7
    "materialise":          _handle_materialise,
    "index_sync":           _handle_index_sync,
    "commit_pending":       _handle_commit_pending,
    # Phase 8
    "housekeeping":         _handle_housekeeping,
    # Phase 9
    "news_watch_submit":    _handle_news_watch_submit,
    "news_watch_finalize":  _handle_news_watch_finalize,
    # Phase 9 — Integrations
    "email":                _handle_email,
    "telegram":             _handle_telegram,
    "jira_create":          _handle_jira_create,
    "jira_sync":            _handle_jira_sync,
}


def dispatch(task_type: str, payload: dict, conn: sqlite3.Connection):
    handler = HANDLERS.get(task_type)
    if handler is None:
        raise ValueError(f"Unknown task_type: {task_type!r}")
    handler(payload, conn)
