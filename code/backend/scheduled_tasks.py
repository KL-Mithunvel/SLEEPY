"""
Job registry: task_type + APScheduler trigger config.

Each entry:
    task_type     — string key matched in task_handlers.HANDLERS
    trigger       — "cron" | "interval"
    trigger_kwargs — passed as **kwargs to scheduler.add_job(trigger, ...)
    payload       — dict passed to the handler
    enabled       — optional bool; if False the job is not registered (default True)

The worker reads this list at startup and registers each enabled job.
"""

import config

SCHEDULED_TASKS = [
    # ------------------------------------------------------------------
    # Nightly MD re-index (legacy cron — kept for explicit full reindex)
    # ------------------------------------------------------------------
    {
        "task_type": "md_reindex",
        "trigger": "cron",
        "trigger_kwargs": {"hour": 2, "minute": 0},
        "payload": {"full": True},
    },

    # ------------------------------------------------------------------
    # Morning briefing at 06:30 IST daily
    # ------------------------------------------------------------------
    {
        "task_type": "morning_briefing",
        "trigger": "cron",
        "trigger_kwargs": {"hour": 6, "minute": 30},
        "payload": {},
    },

    # ------------------------------------------------------------------
    # Incremental ChromaDB sync every INDEX_SYNC_INTERVAL_SEC (default 5 min)
    # ------------------------------------------------------------------
    {
        "task_type": "index_sync",
        "trigger": "interval",
        "trigger_kwargs": {"seconds": config.INDEX_SYNC_INTERVAL_SEC},
        "payload": {},
    },

    # ------------------------------------------------------------------
    # Batch git commit of pending user edits — every hour
    # ------------------------------------------------------------------
    {
        "task_type": "commit_pending",
        "trigger": "cron",
        "trigger_kwargs": {"minute": 0},
        "payload": {},
    },

    # ------------------------------------------------------------------
    # Nightly housekeeping at 23:00 IST
    # ------------------------------------------------------------------
    {
        "task_type": "housekeeping",
        "trigger": "cron",
        "trigger_kwargs": {"hour": 23, "minute": 0},
        "payload": {},
    },

    # ------------------------------------------------------------------
    # News Watch — nightly batch submission at midnight IST
    # Disabled when PMA_NEWS_WATCH_CRON_DISABLED=1 (manual trigger still works)
    # ------------------------------------------------------------------
    {
        "task_type": "news_watch_submit",
        "trigger": "cron",
        "trigger_kwargs": {"hour": 0, "minute": 0},
        "payload": {},
        "enabled": not config.NEWS_WATCH_CRON_DISABLED,
    },

    # ------------------------------------------------------------------
    # News Watch — poll for batch results every 5 min
    # ------------------------------------------------------------------
    {
        "task_type": "news_watch_finalize",
        "trigger": "interval",
        "trigger_kwargs": {"seconds": 300},
        "payload": {},
    },

    # ------------------------------------------------------------------
    # Jira sync every 15 min — only if JIRA_BASE_URL is configured
    # ------------------------------------------------------------------
    {
        "task_type": "index_sync",          # reuses index_sync until jira_sync handler is built in Phase 11
        "trigger": "interval",
        "trigger_kwargs": {"seconds": 900},
        "payload": {"source": "jira"},
        "enabled": bool(config.JIRA_BASE_URL),
    },
]
