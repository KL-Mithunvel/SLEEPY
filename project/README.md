# Project Management Assistant (PMA) — Complete Documentation

This folder contains exhaustive documentation of the **PMA (ProjectManagementAssistant)** system, sufficient to rebuild it from scratch. The PMA source repo is read-only; all documentation here was derived from careful analysis of that codebase.

## What is PMA?

PMA (`pa.mspv.app`) is a self-hosted personal AI project assistant:
- **Flask** backend + **Vue 3** frontend + **Anthropic Claude API**
- **Markdown + Git** as the single source of truth for all user content
- Per-user data isolation (each user gets their own corpus, vector store, and database)
- Calm, focused interface for daily work management, planning, and AI assistance
- Current version: `0.1.41`

## Documentation Index

| File | Contents |
|------|----------|
| [01-overview.md](01-overview.md) | System overview, tech stack, design philosophy, repository layout, key design rules |
| [02-architecture.md](02-architecture.md) | System architecture, request flows, data flows, component interactions |
| [03-backend-modules.md](03-backend-modules.md) | Every backend Python module documented in detail |
| [04-api-reference.md](04-api-reference.md) | All REST API endpoints with request/response schemas |
| [05-md-corpus-conventions.md](05-md-corpus-conventions.md) | Markdown corpus conventions, file formats, task syntax, git conventions |
| [06-configuration-reference.md](06-configuration-reference.md) | All configuration variables, secrets, dev setup, testing |
| [07-frontend.md](07-frontend.md) | Vue 3 frontend: views, components, auth flow, API patterns |
| [08-deployment.md](08-deployment.md) | Docker Compose, Dockerfiles, Caddy, Keycloak setup, upgrade process |
| [09-worker-and-scheduler.md](09-worker-and-scheduler.md) | APScheduler jobs, task queue, background processing |
| [10-ai-pipeline.md](10-ai-pipeline.md) | Claude API integration, RAG, tools, pma-edit format, skills system |
| [11-mcp-server.md](11-mcp-server.md) | MCP 2025-06-18 server, tools, resources, OAuth 2.0 for Claude.ai |
| [12-dependencies.md](12-dependencies.md) | All Python and npm dependencies with purposes and versions |
| [13-integrations.md](13-integrations.md) | Jira, Office 365 email, Telegram, Keycloak, Caddy, git remote |
| [14-todo-and-pending.md](14-todo-and-pending.md) | Pending features, known limitations, settled design decisions |
| [15-news-watch.md](15-news-watch.md) | Complete news watch system: two-stage async pipeline, topics config, dedup, feedback, ABOUT.md |
| [16-project-documentation-guide.md](16-project-documentation-guide.md) | How to document each project: frontmatter fields, every section, best practices, examples |
| [17-recurring-tasks-and-progress-tracking.md](17-recurring-tasks-and-progress-tracking.md) | Full recurring task system: Recur files, plan hierarchy, carry-forward, materialiser stages, Govern |

## Quick Start for Rebuilding

### 1. Repository Structure
```
code/
  backend/          # Python Flask backend
  frontend/         # Vue 3 + Vite frontend
  src/              # Read-only resources (prompts, skills, templates, help)
  pyproject.toml    # Backend dependencies (managed by uv)
tooling/
  build/
    Dockerfile.backend      # Backend Docker image
    Dockerfile.frontend     # Frontend Docker image (if exists)
    example-docker-compose.yml
    src/
      gunicorn.conf.py
docs/               # Project planning docs (charter, architecture, starter)
VERSION             # Semver version string
TODO.md             # Pending features
```

### 2. Key Files to Create First
1. `code/backend/config.py` — configuration + `CurrentUser` dataclass
2. `code/backend/auth_utils.py` — Keycloak JWT validation
3. `code/backend/llm.py` — Claude API wrapper with streaming + tool loop
4. `code/backend/app.py` — Flask factory with blueprints
5. `code/backend/indexing_service.py` — ChromaDB + LlamaIndex RAG
6. `code/backend/md_patcher.py` — pma-edit format + git commits
7. `code/backend/materialiser.py` — deterministic recurring task pipeline
8. `code/backend/task_queue.py` — SQLite task queue
9. `code/backend/worker.py` — APScheduler background worker
10. `code/backend/skills.py` — skills system
11. `code/backend/blueprints/ai.py` — chat endpoint (SSE)
12. `code/backend/blueprints/corpus.py` — corpus REST API
13. `code/backend/blueprints/mcp_server.py` — MCP server
14. `code/frontend/src/main.js` — Vue bootstrap + Keycloak auth
15. `code/frontend/src/views/TodayView.vue` — primary interface

### 3. Non-Negotiable Design Rules
- AI edits committed as `Arivu Baalan <arivu@smtw.in>` with `AI:` prefix
- pma-edit SEARCH/REPLACE (not unified diff) — see [10-ai-pipeline.md](10-ai-pipeline.md)
- Per-user data at `DATA_ROOT/<username>/` — never mix user data
- No LLM calls in materialiser — must be deterministic
- App venv (`code/.venv/`) and tooling venv (`tooling/.venv/`) are strictly separate
- Prompt caching: tag last system block with `cache_control: {type: "ephemeral"}`
- `DEV_AUTH_BYPASS=1` for local dev (NEVER in production)

### 4. Critical Configuration
```python
# secrets_app.py (never committed, mounted :ro in Docker)
ANTHROPIC_API_KEY = "sk-ant-..."
KEYCLOAK_REALM_URL = "https://sso.mspv.app/realms/Office"
DATA_ROOT = "/data"
```

### 5. Data Layout Per User
```
DATA_ROOT/<username>/
  md/                    # Git repo: markdown corpus (Projects, Daily, Plans, Recur, Govern)
  db/
    pma.sqlite3          # Operational: chat history, settings, news feedback
    chroma/              # ChromaDB: vector index (collection: md_corpus)
DATA_ROOT/
  queue.sqlite3          # Shared: task queue for email/telegram/jira/news
```

## Source Repository

The PMA source code lives at: `smtwkla/project-management-assistant` (read-only reference)

This documentation was created from the source at version `0.1.41`.
