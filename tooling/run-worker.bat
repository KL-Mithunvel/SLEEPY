@echo off
REM Starts the background task worker (APScheduler + task_queue drainer).
REM Never run this inside the same process as the web server.
cd /d "%~dp0..\code\backend"
set DEV_AUTH_BYPASS=1
uv run python worker.py
