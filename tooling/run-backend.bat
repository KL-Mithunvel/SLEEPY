@echo off
REM Starts BOTH backend (Flask) and frontend (Vite) for local dev/testing.
cd /d "%~dp0..\code\backend"
set DEV_AUTH_BYPASS=1
set DEBUG=1
uv run python main.py
