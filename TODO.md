# TODO

## In Progress

## Done
- [x] Phase 0 — Project scaffold: directory structure, uv backend init, Vue 3 + Vite frontend, Docker Compose dev, secrets pattern, .gitignore, .bat wrappers
- [x] Phase 1 — Backend foundation: Flask app, SQLite migrations, RBAC (config_rbac.py + auth_utils.py), task queue, worker skeleton, slow-request logger, 9/9 tests passing
- [x] Infra — Moved dev entry point (`main.py`), `pyproject.toml`, `uv.lock`, `.python-version`, `requirements.txt` to repo root; single root `.venv` (`uv sync`) replaces the old `code/backend/.venv`; fixed `SQLITE_DB_PATH` to resolve against the backend dir instead of process cwd so it survives the move

## Not Started
- [x] Phase 2 — Frontend skeleton: Keycloak JS auth store, api.js helper, sidebar/topbar layout, PWA manifest, Vue Router, dark mode Bootstrap theme — build passes, dev bypass working
- [x] Phase 3 — AI layer: LiteLLM + ChromaDB + LlamaIndex MD indexing, safe MD edit flow (diff → patch → GitPython commit), ai_events logging, Flask blueprint (`/api/ai/*`), 54/54 tests passing
- [x] Phase 4 — Core features: Today View (briefing card, task list, quick capture → inbox.md), Projects view (OU-grouped, status badges, task counts, inline content), Logs view (daily/weekly filter, inline reader), 82/82 tests passing
- [x] AI Assistant view — natural language chat, edit proposals with diff + Apply/Discard, retry/clarify flow
- [ ] Phase 5 — Integrations: WhatsApp notifications + outbound messaging (Twilio or Meta Cloud API), email support (Gmail MCP or Graph API)
- [ ] Phase 6 — Deploy: production Docker Compose, Caddy HTTPS, nginx pa.mspv.app, Keycloak pma client, mobile PWA test
