@echo off
REM Starts backend (Flask), frontend (Vite), and the worker (APScheduler/task_queue) for local dev/testing.
cd /d "%~dp0.."
set DEV_AUTH_BYPASS=1
set DEBUG=1
uv run python main.py
