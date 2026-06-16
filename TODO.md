# TODO

## In Progress

## Done
- [x] Phase 0 — Project scaffold: directory structure, uv backend init, Vue 3 + Vite frontend, Docker Compose dev, secrets pattern, .gitignore, .bat wrappers
- [x] Phase 1 — Backend foundation: Flask app, SQLite migrations, RBAC (config_rbac.py + auth_utils.py), task queue, worker skeleton, slow-request logger, 9/9 tests passing

## Not Started
- [x] Phase 2 — Frontend skeleton: Keycloak JS auth store, api.js helper, sidebar/topbar layout, PWA manifest, Vue Router, dark mode Bootstrap theme — build passes, dev bypass working
- [ ] Phase 3 — AI layer: LiteLLM + ChromaDB + LlamaIndex MD indexing, safe MD edit flow (diff → patch → GitPython commit), ai_events logging
- [ ] Phase 4 — Core features: Today View, Project Dashboard, Morning Briefing generator, Task Capture (quick-capture to inbox.md)
- [ ] Phase 5 — Integrations: email support (Gmail MCP or Graph API), nightly scheduled jobs
- [ ] Phase 6 — Deploy: production Docker Compose, Caddy HTTPS, nginx pa.mspv.app, Keycloak pma client, mobile PWA test
