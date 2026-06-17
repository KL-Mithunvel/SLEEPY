# Claude Log

## 2026-06-18 — Document full project state ahead of Phase 3
- Read PROJ_CHARTER.md, PROJ_STARTER.md, and all backend modules (app.py, config.py, auth_utils.py, config_rbac.py, local_db.py, task_queue.py, task_handlers.py, worker.py, scheduled_tasks.py, db_helpers.py, test files) to establish ground truth.
- Wrote full `.CLAUDE/CLAUDE.md` replacing the empty template: all sections filled in (Project Overview, Running the System, Architecture, Key Modules, Schema Reference, Key Conventions, Data Files, Platform Constraints, Deployment Notes, Known Technical Debt, 10 Development Rules, Project TODO list, User Rules + overrides).
- Key things documented that were previously unwritten: the main.py-at-root layout quirk + sys.path insertion, SQLITE_DB_PATH relative-to-backend-dir anchoring fix, per-user data layout, ai_events immutability rule, task handler no-commit rule, AI commit author identity.
- Technical debt catalogued: 5 items, most significant being the two placeholder task handlers (Phase 3 work) and missing bat wrappers referenced in charter §10.

## 2026-06-17 — Move dev entry point and venv to repo root
- Moved `code/backend/main.py` → `main.py` (root). Added a `sys.path` insert for `code/backend` so its bare imports (`config`, `local_db`, `app`) keep resolving; fixed `FRONTEND_DIR` to point at `code/frontend`.
- Moved `pyproject.toml`, `uv.lock`, `.python-version`, `requirements.txt` from `code/backend/` to root; deleted the stray duplicate `requirement.txt` at root.
- Fixed `local_db.py`: `SQLITE_DB_PATH` (relative path from `secrets_app.py`) was being resolved against the process cwd, which broke once the entry point moved out of `code/backend`. Now anchored to the backend dir, same pattern `config.py` already uses for `USER_DATA_ROOT`.
- Deleted old `code/backend/.venv`; ran `uv sync` at root to create the new `.venv` and install all 138 deps from the lock file.
- Updated `tooling/run-backend.bat` to run from repo root instead of `cd`-ing into `code/backend`.
- Verified: import smoke test + a real `uv run python main.py` boot — `/healthz` returned 200, Vite dev server also started clean.
- Found but did not change: `BATTLESHIP/.venv/Scripts` is on this machine's PATH ahead of everything else, so a bare `python`/`pip` outside an activated venv silently uses BATTLESHIP's packages. Likely the real cause of the original "library issues" — flagged to the user, no system PATH change made without confirmation.

