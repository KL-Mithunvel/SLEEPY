@echo off
REM One-shot full re-index of the MD corpus into ChromaDB.
REM Use this to bootstrap or rebuild the vector index from scratch.
cd /d "%~dp0..\code\backend"
set DEV_AUTH_BYPASS=1
uv run python md_indexer.py
