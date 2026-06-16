"""
Cron registry: task_type + APScheduler cron expression.
APScheduler enqueues into task_queue rather than running work inline.
"""

SCHEDULED_TASKS = [
    # Morning briefing at 06:30 IST daily
    {
        "task_type": "morning_briefing",
        "cron": {"hour": 6, "minute": 30},
        "payload": {},
    },
    # Nightly MD re-index at 02:00 IST
    {
        "task_type": "md_reindex",
        "cron": {"hour": 2, "minute": 0},
        "payload": {"full": True},
    },
]
