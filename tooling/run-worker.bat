@echo off
REM Starts the background task worker (APScheduler + task_queue drainer) standalone.
REM main.py already spawns this automatically as its own subprocess — use this
REM wrapper only if you want the worker running without the web server/frontend
REM (e.g. testing scheduled jobs in isolation). Never run two workers at once
REM (duplicate APScheduler cron firings would double-enqueue tasks).
cd /d "%~dp0..\code\backend"
set DEV_AUTH_BYPASS=1
uv run python worker.py
