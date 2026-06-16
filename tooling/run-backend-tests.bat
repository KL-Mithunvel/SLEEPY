@echo off
cd /d "%~dp0..\code\backend"
uv run pytest %*
