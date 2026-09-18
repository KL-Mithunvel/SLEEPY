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
    """
    Refresh project deadline plans, generate the morning briefing, log it to
    ai_events, and email the combined digest (briefing + deadlines) to the user.
    """
    logger.info("morning_briefing started")

    digest_lines: list[str] = []
    try:
        import goal_planner
        goal_result = goal_planner.run_goal_planning(config.USER_DATA_ROOT, user_nick=config.USER_NICK)
        digest_lines = goal_result.get("digest_lines", [])
        logger.info("morning_briefing: goal_planning done: %s", goal_result)
    except Exception:
        logger.exception("morning_briefing: goal_planning failed")

    briefing = ai_client.generate_morning_briefing(conn)
    logger.info("morning_briefing done (%d chars)", len(briefing))

    combined_text = briefing
    if digest_lines:
        combined_text += "\n\n---\n\n## ⏰ Project Deadlines\n\n" + "\n".join(digest_lines)

    try:
        import integrations
        from datetime import date
        today_str = date.today().strftime("%d-%m-%Y")
        ok = integrations.send_email(
            config.USER_EMAIL, f"Morning Briefing — {today_str}", combined_text,
        )
        if not ok:
            logger.warning("morning_briefing: email not sent (integration not configured or failed)")
    except Exception:
        logger.exception("morning_briefing: email send failed")


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
        import md_editor

        data_root = config.USER_DATA_ROOT
        try:
            repo = git.Repo(data_root, search_parent_directories=False)
        except git.InvalidGitRepositoryError:
            logger.info("commit: no git repo at %s, skipping", data_root)
            return

        # Serialised against the web process's own commits (md_editor.apply_edit)
        # and guaranteed never to sweep db/ (SQLite + Chroma + news state) into
        # the corpus history — `git add -A` is only safe once db/ is ignored.
        with md_editor.corpus_git_lock(data_root):
            md_editor.ensure_corpus_gitignore(data_root)

            status = repo.git.status("--porcelain")
            if not status.strip():
                logger.info("commit: repo is clean, nothing to commit")
                return

            repo.git.add(A=True)

            from datetime import datetime, timedelta, timezone
            _IST = timezone(timedelta(hours=5, minutes=30))
            ts = datetime.now(_IST).strftime("%Y-%m-%dT%H:%M IST")
            author = md_editor.ai_actor()
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
    # login_events is written by unauthenticated requests (every failed attempt
    # against any username) — keep 90 days for the admin view, drop the rest so
    # a junk flood can't grow the DB without bound.
    conn.execute(
        """
        DELETE FROM login_events
        WHERE created_at < datetime('now', '-90 days', 'localtime')
        """
    )
    logger.info("housekeeping: pruned old task_queue + login_events rows")

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
    force_all = bool((payload or {}).get("force_all"))
    result = news_watch.news_watch_submit_for_user(config.USER_DATA_ROOT, force_all=force_all)
    logger.info("news_watch_submit: %s", result)


def _handle_news_watch_finalize(payload: dict, conn: sqlite3.Connection):
    """Poll Anthropic batch and write surviving bullets to inbox.md."""
    import news_watch
    result = news_watch.news_watch_finalize_for_user(config.USER_DATA_ROOT)
    logger.info("news_watch_finalize: %s", result)


# ---------------------------------------------------------------------------
# Phase 9 — Integrations (O365 email)
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


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------

def _handle_db_backup(payload: dict, conn: sqlite3.Connection):
    """
    Nightly WAL-safe SQLite backup to db/backups/ (under the same db/ folder
    that's already gitignored in the corpus repo), then prune anything older
    than config.DB_BACKUP_RETENTION_DAYS. This is the SQLite half of the
    "Layer 5 — Backup & recovery" plan in docs/SECURITY_REVIEW.md — the MD
    corpus itself already has full history via its own git repo and doesn't
    need a separate backup path.
    """
    import datetime
    import os

    import local_db

    db_dir = os.path.dirname(local_db.db_path())          # .../db/sqlite
    backup_dir = os.path.join(os.path.dirname(db_dir), "backups")  # .../db/backups
    now = datetime.datetime.now()
    dest = os.path.join(backup_dir, f"pma-{now.strftime('%Y%m%d-%H%M')}.db")

    local_db.backup_database(dest)
    logger.info("db_backup: wrote %s", dest)

    cutoff = now - datetime.timedelta(days=config.DB_BACKUP_RETENTION_DAYS)
    pruned = 0
    for name in os.listdir(backup_dir):
        if not (name.startswith("pma-") and name.endswith(".db")):
            continue
        path = os.path.join(backup_dir, name)
        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            continue
        if mtime < cutoff:
            try:
                os.remove(path)
                pruned += 1
            except OSError:
                logger.warning("db_backup: could not prune %s", path)
    if pruned:
        logger.info("db_backup: pruned %d backup(s) older than %d days", pruned, config.DB_BACKUP_RETENTION_DAYS)


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
    # Backups
    "db_backup":            _handle_db_backup,
}


def dispatch(task_type: str, payload: dict, conn: sqlite3.Connection):
    handler = HANDLERS.get(task_type)
    if handler is None:
        raise ValueError(f"Unknown task_type: {task_type!r}")
    handler(payload, conn)
