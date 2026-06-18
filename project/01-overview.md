# PMA — Project Overview

**Full name:** ProjectManagementAssistant (working name)
**Production URL:** `https://pa.mspv.app`
**Version:** 0.1.41
**Status:** Internal beta
**Started:** 2026-04-14

---

## 1. What PMA Is

PMA is a **self-hosted, single-user personal AI project assistant** that reconnects day-to-day tasks to the bigger picture. It combines a Markdown corpus (the user's project files, daily logs, plans, and reviews) with an AI companion (Claude, via the Anthropic API) that reads that corpus, surfaces "why this task matters", drafts messages and documents, and safely patches Markdown files through a structured SEARCH/REPLACE mechanism committed to Git.

PMA is not another task manager. Upstream execution systems (Jira, ERPNext, etc.) continue to own task execution. PMA provides the context layer on top: purpose, narrative, planning, delegation, and reflection.

### What PMA Does

- **Today View** — surfaces the user's top 3–5 tasks for the day, each annotated with a one-line "why it matters" pulled from the project corpus.
- **AI Companion** — a chat interface backed by Claude. The assistant reads the user's Markdown corpus via RAG (ChromaDB), answers questions about projects and plans, and produces draft-ready artifacts (email bodies, Telegram messages, Markdown updates).
- **Safe AI MD Editing** — the LLM returns edits as `pma-edit` SEARCH/REPLACE blocks. The backend locates the exact match in the file, applies the replacement, and commits the result to Git under the assistant's authorship.
- **Nightly Materialisation** — a deterministic (no LLM) pipeline that seeds Daily logs, plan files, and Governance files from recurring task definitions.
- **Background Indexing** — the worker process keeps the ChromaDB vector store synchronised with the Markdown corpus on disk, enabling accurate RAG retrieval at chat time.
- **Notifications** — email (Microsoft Graph / O365) and Telegram, enqueued via a SQLite task queue and drained by the worker.
- **Skills** — a progressive-disclosure library of guided workflows (daily review, monthly planning, weekly review, project setup, email triage, meeting prep, etc.) that the AI loads on demand.
- **MCP Server** — exposes corpus tools to external LLM clients (e.g. Claude.ai connectors, Cursor).

### What PMA Is Not

- Not a general-purpose note-taking app — scoped to a single user's project portfolio.
- Not a rich text editor — Markdown is edited by the AI or directly in Git.
- Not a task execution system — it reads Jira, it does not replace it.
- No gamification (no XP, streaks, or badges).
- No real-time collaboration or WebSockets.

---

## 2. Design Philosophy

### Markdown + Git as Single Source of Truth

Every piece of project context lives in a Git-managed Markdown corpus. Decisions, plans, reviews, daily logs, and team notes are all `.md` files. Git provides version history, authorship, and rollback. ChromaDB is a derived index — rebuildable from the MD corpus at any time. SQLite is app state only (chat history, task queue, job logs) — deletable and rebuildable. There is no database schema that holds irreplaceable information.

**Why Markdown:** human-readable, diffable, editable with any tool, portable. No vendor lock-in. The user can read, edit, and backup their corpus independently of PMA.

**Why Git:** every AI edit is a commit. The user can `git log` to see what the assistant changed and `git revert` to undo it. The assistant's authorship (`Arivu Baalan <arivu@smtw.in>`) is distinct from the user's authorship, so the history is clear.

### Per-User Data Isolation

All user data lives under `DATA_ROOT/<username>/`. There is no shared SQLite, no shared ChromaDB, and no cross-user data. The layout supports multiple users from day one even though the system is single-user in its current deployment. A full user backup is a single `rsync` of their folder.

### Calm AI Assistant

The AI is purpose-first and calm. It avoids noise, gamification, and unsolicited notifications. It surfaces context when asked, drafts artifacts on demand, and commits edits only when explicitly instructed. The system prompt (hot-reloaded from `code/src/prompts/SystemPrompt.MD`) can be updated without restarting the server.

### Deterministic Materialisation

The nightly pipeline that seeds Daily files, plan files, and Governance files is entirely deterministic — no LLM is involved. This ensures idempotency, predictability, and zero API cost for routine operations. The LLM is reserved for interactive tasks where its reasoning adds value.

### Secrets and Configuration

Secrets (API keys, Keycloak config) live in `secrets_app.py` which is gitignored and mounted read-only into containers at deploy time. An `example_secrets_app.py` is checked in as a template. All config reads flow through `config.py`. Environment variables take precedence over `secrets_app.py` values.

---

## 3. Technology Stack

### Backend

| Component | Technology | Notes |
|---|---|---|
| Language | Python 3.12+ | Required minimum version |
| Web framework | Flask | Factory pattern (`create_app()`) |
| WSGI server | gunicorn | Production server |
| Package manager | uv | `pyproject.toml` based |
| Venv | `code/.venv/` | App venv (runtime only, no pytest) |

**Key Python dependencies** (`code/pyproject.toml`):

```
anthropic>=0.95.0
apscheduler>=3.11.2
chromadb>=1.5.7
fastembed>=0.8.0
flask>=3.1.3
flask-cors>=6.0.2
gitpython>=3.1.46
jinja2>=3.1
llama-index-core>=0.14.20
llama-index-embeddings-fastembed>=0.6.0
llama-index-readers-file>=0.6.0
llama-index-vector-stores-chroma>=0.5.5
msal>=1.36.0
pyjwt[crypto]>=2.12.1
python-dotenv>=1.2.2
gunicorn>=21.0
```

### Frontend

| Component | Technology | Version |
|---|---|---|
| Framework | Vue 3 | ^3.4.0 |
| Build tool | Vite | ^7.0.0 |
| Dev server port | — | 5173 |
| CSS framework | Bootstrap | 5.3.3 |
| Icons | bootstrap-icons | ^1.11.3 |
| Markdown renderer | markdown-it | ^14.1.1 |
| Router | vue-router | ^4.3.0 |
| Auth | keycloak-js | ^26.2.3 |
| Test runner | vitest | ^3.2.0 |

The frontend is a **Vue 3 SPA** built with Vite and served as static files by nginx in the production container. In development, Vite's dev server (port 5173) proxies `/api/*` requests to the Flask backend at port 5000.

**Frontend routes:**

| Path | View | Description |
|---|---|---|
| `/today` | Today.vue | Today's tasks and AI companion |
| `/q-plan` | QPlan.vue | Quarterly plan view |
| `/projects` | Projects.vue | Project browser |
| `/team` | Team.vue | Team and Governance view |
| `/files` | Files.vue | Corpus file browser and history |
| `/search` | Search.vue | Full-text and semantic search |
| `/settings` | Settings.vue | User preferences and MCP config |

**Frontend stores** (Pinia / reactive):
- `auth.js` — Keycloak auth state, token refresh
- `corpus.js` — corpus metadata, OU selection
- `ou.js` — active Organisational Unit
- `plans.js` — plan file data

**Frontend composables:**
- `useProjectFile.js` — project file read/write
- `useTaskContent.js` — task toggling within files

### AI Layer

| Component | Technology | Notes |
|---|---|---|
| LLM provider | Anthropic Claude API | Primary and only provider |
| Model | `claude-sonnet-4-6` | `DEFAULT_MODEL` in `llm.py` |
| Vector index | LlamaIndex | `MarkdownNodeParser` for MD-aware splitting |
| Vector store | ChromaDB | Per-user persistent collection `md_corpus` |
| Embeddings | fastembed | `BAAI/bge-small-en-v1.5` ONNX model (~100MB cache) |
| Prompt caching | Anthropic ephemeral cache | `cache_control: {type: "ephemeral"}` on last system block |
| Max tool iterations | 8 | Safety cap on tool-use loop |
| Tool result truncation | 60,000 chars | Context bounding |

### Authentication

| Component | Technology | Notes |
|---|---|---|
| Provider | Keycloak | Realm: `Office`, Client: `pma` |
| Flow | PKCE S256 | Implemented in keycloak-js (frontend) |
| Token validation | PyJWT + JWKS | RS256, JWKS fetched from Keycloak's certs endpoint |
| Roles | Keycloak realm + resource | `realm_access.roles` + `resource_access.<client>.roles` |
| `aud` claim | Not verified | Keycloak default `aud=account`; `azp` claim checked instead |
| Internal URL rewrite | Yes | `https://sso.mspv.app` → `http://<KEYCLOAK_HOST_IP>:8080` |
| Dev bypass | `DEV_AUTH_BYPASS=1` | Sets user from `DEV_USER` env, no Keycloak needed |

### Storage

| Store | Technology | Purpose |
|---|---|---|
| Markdown corpus | Files in Git repo | Single source of truth — projects, plans, logs |
| Operational DB | SQLite (`pma.sqlite3` per user) | Chat history, task queue, job logs |
| Task queue | SQLite (`queue.sqlite3` shared) | Email, Telegram, Jira creation tasks |
| Vector store | ChromaDB (per user) | MD corpus embeddings for RAG |

### Background Jobs

| Component | Technology | Notes |
|---|---|---|
| Scheduler | APScheduler | `BackgroundScheduler` in separate worker process |
| Email | Microsoft Graph API (MSAL) | O365 client credentials flow |
| Telegram | Telegram Bot API | Direct HTTP calls |
| Jira | Jira Cloud REST API | Read issues, create issues, auto-check done |

### Deployment

| Component | Technology | Notes |
|---|---|---|
| Containers | Docker Compose | 3 containers: backend, worker, frontend |
| Reverse proxy | Caddy | On host (not in compose), HTTPS + Let's Encrypt |
| Backend image | `pma-backend` | Flask + gunicorn, Python 3.12+ |
| Worker | Same image as backend | Entrypoint: `python -m backend.worker` |
| Frontend image | `pma-frontend` | Vue static assets + nginx |
| Data volume | `${DATA_ROOT}:/data` | Bind-mounted into backend and worker |
| Secrets | `secrets_app.py` mounted `:ro` | Never baked into the image |

---

## 4. Repository Layout

```
ProjectManagementAssistant/
├── VERSION                         # Semver version file (e.g. "0.1.41")
├── TODO.md                         # Living list of pending features
│
├── code/                           # Application (single uv project)
│   ├── pyproject.toml              # Runtime deps ONLY (no pytest, no dev tools)
│   ├── uv.lock                     # Locked deps
│   ├── .venv/                      # App venv (prod-like)
│   ├── index.html                  # Vite entry point for frontend
│   │
│   ├── backend/                    # Flask Python package
│   │   ├── __init__.py
│   │   ├── app.py                  # Flask factory + auth gate + blueprint registration
│   │   ├── config.py               # Central config — paths, secrets, CurrentUser dataclass
│   │   ├── auth_utils.py           # Keycloak JWT validation (JWKS + rotation retry)
│   │   ├── db.py                   # DB connection helpers
│   │   ├── llm.py                  # Claude API wrapper (caching + tool loop + streaming)
│   │   ├── chat_history.py         # SQLite-backed chat persistence
│   │   ├── indexing_service.py     # Per-user LlamaIndex + ChromaDB + fastembed
│   │   ├── md_patcher.py           # SEARCH/REPLACE apply + git commit + CRLF normalisation
│   │   ├── md_grep.py              # Full-text corpus search (tool for AI)
│   │   ├── materialiser.py         # Deterministic Recur → Plans → Daily → Govern pipeline
│   │   ├── email_service.py        # Microsoft Graph API (MSAL) email sender
│   │   ├── telegram_service.py     # Telegram Bot API wrapper
│   │   ├── task_queue.py           # SQLite task queue with retry + exponential backoff
│   │   ├── skills.py               # Progressive-disclosure skill loader for LLM
│   │   ├── templates_reader.py     # Read-only access to src/templates/user/md/
│   │   ├── help_reader.py          # Read-only access to src/help/
│   │   ├── jira_service.py         # Jira Cloud REST API client
│   │   ├── news_watch.py           # Anthropic Batch API news watch
│   │   ├── playbook.py             # Playbook runner
│   │   ├── housekeeping.py         # Daily corpus health checks + archive sweep
│   │   ├── worker.py               # APScheduler worker process entrypoint
│   │   ├── example_secrets_app.py  # Checked-in secrets template
│   │   ├── secrets_app.py          # NOT committed — real secrets (gitignored)
│   │   │
│   │   ├── blueprints/             # Flask blueprints (one per API area)
│   │   │   ├── __init__.py
│   │   │   ├── health.py           # GET /api/health
│   │   │   ├── auth.py             # GET /api/auth/{config,me}
│   │   │   ├── ai.py               # POST /api/ai/chat (SSE streaming)
│   │   │   ├── corpus.py           # /api/corpus/* (files, plans, recur, govern, people)
│   │   │   ├── jira.py             # POST /api/jira/push (enqueue Jira creation)
│   │   │   └── mcp_server.py       # POST /mcp (MCP 2025-06-18 JSON-RPC)
│   │   │
│   │   └── tools/                  # Post-reply action handlers
│   │       ├── __init__.py         # TOOL_REGISTRY + dispatch()
│   │       ├── base.py             # ToolAction dataclass
│   │       └── md_edit.py          # pma-edit block handler
│   │
│   ├── frontend/                   # Vue 3 + Vite SPA
│   │   ├── package.json
│   │   ├── vite.config.js
│   │   └── src/
│   │       ├── main.js             # App entry, router setup, auth bootstrap
│   │       ├── App.vue             # Root component, sidebar + router-view
│   │       ├── api.js              # Axios/fetch API helpers
│   │       ├── assets/             # CSS, images
│   │       ├── views/              # Top-level page components
│   │       │   ├── Today.vue
│   │       │   ├── QPlan.vue
│   │       │   ├── Projects.vue
│   │       │   ├── Team.vue        # Govern + People
│   │       │   ├── Files.vue       # File browser + History tab
│   │       │   ├── Search.vue
│   │       │   └── Settings.vue
│   │       ├── components/         # Reusable UI components
│   │       │   ├── AppSidebar.vue
│   │       │   ├── ChatPanel.vue
│   │       │   ├── DiaryPanel.vue
│   │       │   ├── InboxPanel.vue
│   │       │   ├── FileTree.vue
│   │       │   ├── TaskBlocks.vue
│   │       │   ├── ProjectForm.vue
│   │       │   ├── ProjectList.vue
│   │       │   ├── OuSelector.vue
│   │       │   └── MoveTool.vue
│   │       ├── stores/             # Reactive state stores
│   │       │   ├── auth.js
│   │       │   ├── corpus.js
│   │       │   ├── ou.js
│   │       │   └── plans.js
│   │       └── composables/        # Reusable composition functions
│   │           ├── useProjectFile.js
│   │           └── useTaskContent.js
│   │
│   ├── src/                        # Read-only resources (not user data)
│   │   ├── prompts/
│   │   │   ├── SystemPrompt.MD     # Hot-reloaded AI system prompt
│   │   │   └── skills/             # 9 skill files loaded on demand by LLM
│   │   │       ├── daily-review.md
│   │   │       ├── weekly-review.md
│   │   │       ├── monthly-planning.md
│   │   │       ├── quarterly-planning.md
│   │   │       ├── project-setup.md
│   │   │       ├── email-triage.md
│   │   │       ├── meeting-prep.md
│   │   │       ├── monthly-compliance.md
│   │   │       └── people-delegation.md
│   │   ├── help/                   # LLM-readable and user-readable help docs
│   │   │   ├── Playbook-Format.md
│   │   │   └── Recur-Format.md
│   │   └── templates/
│   │       └── user/               # Seed templates copied to data/<user>/ on onboarding
│   │           ├── ABOUT.md
│   │           └── md/             # Templates the LLM reads via templates_reader
│   │
│   └── tests/                      # pytest suite
│
├── tooling/                        # Separate uv project for dev/build scripts
│   ├── pyproject.toml              # Editable install of ../code + dev tools (pytest)
│   ├── uv.lock
│   ├── .venv/                      # Dev/test venv (pytest + code editable)
│   ├── prod-img-update.py          # SSH to prod, docker compose pull + restart
│   ├── sync-prod-data.py           # Sync MD corpus from prod to local dev
│   └── build/                      # Docker build artefacts
│       ├── Dockerfile.backend
│       ├── Dockerfile.frontend
│       ├── build.bat               # Windows build + push script
│       ├── example-docker-compose.yml
│       ├── example_secrets_registry.bat
│       └── src/                    # Build-time static assets for frontend
│
└── docs/                           # Planning and reference docs
    ├── Project-Charter.MD          # Vision, scope, architecture decisions
    ├── Project-Architecture.MD     # Current implementation reference
    ├── Project-Starter.md          # Reusable baseline patterns
    ├── Keycloak-Setup.MD           # Auth server configuration guide
    └── MCP.md                      # MCP server documentation
```

---

## 5. Key Design Rules

### Dependency Isolation

`code/` and `tooling/` are strictly separate `uv` projects with separate venvs and separate `pyproject.toml` files.

- `code/.venv/` — runtime only. No pytest, no dev tools, no test dependencies.
- `tooling/.venv/` — development and build only. Includes `code/` as an **editable install** so tests can `from backend.app import create_app` without Flask landing in the prod venv.
- Scripts in `tooling/` must not import from `code/` at module level (only via the editable install in the tooling venv).
- App code in `code/` must never import from `tooling/`.

### Per-User Data Isolation

All runtime data lives under `DATA_ROOT/<username>/`:

```
DATA_ROOT/
  queue.sqlite3               # Shared task queue (keyed by user)
  <username>/
    md/                       # Markdown corpus (git repo)
      Projects/
        <OU>/
          <project>.md
      Daily/
        <YYYY-MM-DD>.md
      Plans/
        <OU>/
          <YYYY>-Year.md
          <YYYY>-Q<N>.md
          <YYYY>-<MM>-Month.md
      Recur/
        Daily.md
        Weekly.md
        <period>.md
      Govern/
        <YYYY-MM>.md
      People.md
      Inbox.md
    db/
      pma.sqlite3             # Operational state (chat history, etc.)
      chroma/                 # ChromaDB vector store
```

No shared SQLite, no shared ChromaDB, no cross-user data access.

### AI Edit Authorship

All AI-generated edits committed to the Markdown corpus use a fixed Git actor:

```
Arivu Baalan <arivu@smtw.in>
```

Commit messages use the prefix `AI:` (e.g. `AI: updated project status for KILN-AR26`). MCP-originated edits use `mcp@smtw.in` as the email to distinguish them in `git log`.

User-authored commits use the user's Keycloak email.

### No LLM in Materialiser

The nightly materialisation pipeline (`materialiser.py`) is entirely deterministic. No LLM calls, no API costs, no non-determinism. The pipeline seeds Daily logs, plan files, and Governance files from Recur definitions using stable idempotency markers (`^R:<sha1[:8]>-<period>`). This runs at 00:00 daily via APScheduler.

### pma-edit Format (Not Unified Diff)

The LLM produces edits in a custom SEARCH/REPLACE format rather than unified diff (`@@ -a,b +c,d @@`). LLMs consistently get unified diff line counts wrong. The `pma-edit` format is unambiguous:

```
```pma-edit
file: Projects/KILN/ar26.md
<<<<<<< SEARCH
- [ ] Review proposal
=======
- [x] Review proposal
>>>>>>> REPLACE
```
```

The backend locates the exact match (must be unique), applies the replacement, and commits.

### Prompt Caching

The last system block in every Claude API call is tagged with `cache_control: {type: "ephemeral"}`, activating Anthropic's prompt caching (~5 minute TTL). The system prompt, corpus context, TOC, and skills manifest are all stable across turns, making them good cache targets. Cache hit/miss statistics are included in `ChatResult`.

### Development Auth Bypass

Set `DEV_AUTH_BYPASS=1` in the environment to skip Keycloak entirely. The backend synthesises a `CurrentUser` from `DEV_USER` (default: `"kla"`) and grants `admin` role. No Keycloak server required for local development.

---

## 6. Skills System

Nine skills are available to the AI, each stored as a Markdown file in `code/src/prompts/skills/`. The skills manifest (name + one-line description for each skill) is always included in the system prompt. The skill body is **loaded on demand** via the `load_skill` tool — the LLM calls `load_skill("daily-review")` to get the full instructions for that workflow.

This progressive-disclosure approach keeps the baseline system prompt compact while giving the AI access to detailed guided workflows when needed.

| Skill | File | Purpose |
|---|---|---|
| daily-review | `daily-review.md` | End-of-day review and tomorrow's setup |
| weekly-review | `weekly-review.md` | Weekly retrospective and next-week planning |
| monthly-planning | `monthly-planning.md` | Monthly plan seeding and review |
| quarterly-planning | `quarterly-planning.md` | Quarterly plan creation |
| project-setup | `project-setup.md` | New project file scaffolding |
| email-triage | `email-triage.md` | Email inbox processing |
| meeting-prep | `meeting-prep.md` | Pre-meeting context gathering |
| monthly-compliance | `monthly-compliance.md` | Compliance checklist review |
| people-delegation | `people-delegation.md` | Delegation tracking and follow-up |

---

## 7. API Surface Summary

### Backend API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Health check (public) |
| GET | `/api/auth/config` | Keycloak config for frontend (public) |
| GET | `/api/auth/me` | Current user claims |
| POST | `/api/ai/chat` | SSE streaming chat (LLM + RAG + tool loop) |
| GET | `/api/corpus/ous` | List Organisational Units |
| GET | `/api/corpus/tree` | File tree for an OU |
| GET | `/api/corpus/file` | Read a file |
| PUT | `/api/corpus/file` | Write a file |
| GET | `/api/corpus/plans` | Read plan files |
| GET | `/api/corpus/recur` | Read recur files |
| POST | `/api/corpus/reindex` | Force full re-index |
| GET | `/api/corpus/govern` | Governance file for an OU |
| GET | `/api/corpus/people` | People.md |
| GET | `/api/corpus/queue-stats` | Task queue statistics |
| POST | `/api/jira/push` | Enqueue Jira issue creation |
| POST | `/mcp` | MCP JSON-RPC (2025-06-18 protocol) |
| GET | `/mcp` | MCP SSE hello |
| GET | `/authorize` | OAuth 2.0 authorization (MCP) |
| POST | `/token` | OAuth 2.0 token exchange (MCP) |
| GET | `/.well-known/oauth-authorization-server` | OAuth discovery (public) |
| GET | `/.well-known/oauth-protected-resource` | OAuth resource metadata (public) |

### Chat Tools (available to the LLM during chat)

| Tool | Description |
|---|---|
| `load_skill` | Load the full body of a named skill |
| `grep` | Full-text search across the Markdown corpus |
| `read_file` | Read the full contents of a corpus MD file |
| `read_src` | Read a file from `code/src/` (help docs, templates) |
| `list_src` | List files in a `code/src/` subdirectory |
| `list_files` | List files in the corpus |
| `search_corpus` | Semantic RAG search via ChromaDB |
| `send_email` | Enqueue an email via the task queue |
| `send_telegram` | Enqueue a Telegram message via the task queue |

### MCP Tools (7 tools exposed to external LLM clients)

| Tool | Description |
|---|---|
| `read_file` | Read a corpus MD file |
| `search_corpus` | Semantic search |
| `grep` | Full-text search |
| `list_files` | List corpus files |
| `write_file` | Write/update a corpus file |
| `apply_edit` | Apply a pma-edit block |
| `read_src` | Read from `code/src/` |
| `list_src` | List `code/src/` contents |

### MCP Resources (5 resources exposed)

| URI | Description |
|---|---|
| `pma://system-prompt` | Current system prompt |
| `pma://user-profile` | User's ABOUT.md profile |
| `pma://project-index` | Project index for active OU |
| `pma://template-index` | Available templates |
| `pma://skills` | Skills manifest |

---

## 8. External Integrations

| Integration | Purpose | Auth mechanism |
|---|---|---|
| Anthropic Claude API | Primary LLM | API key (`ANTHROPIC_API_KEY`) |
| Keycloak | OIDC auth | Realm `Office`, client `pma`, PKCE S256 |
| Microsoft Graph (O365) | Email sending | MSAL client credentials (`O365_TENANT_ID`, `O365_CLIENT_ID`, `O365_CLIENT_SECRET`) |
| Telegram Bot API | Notification sending | Bot token (`TELEGRAM_BOT_TOKEN`) |
| Jira Cloud | Issue read/create | API token (`JIRA_API_TOKEN`, `JIRA_USER_EMAIL`) |
| ChromaDB | Vector store | Local persistent client (no external server) |

---

## 9. Environment Variables and Configuration

All configuration is centralised in `code/backend/config.py`. Values are resolved in priority order: environment variable > `secrets_app.py` attribute > default.

### Key Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATA_ROOT` | `<repo>/data` | Root directory for all user data |
| `DEV_AUTH_BYPASS` | `0` | Set to `1` to skip Keycloak in development |
| `DEV_USER` | `kla` | Username used when `DEV_AUTH_BYPASS=1` |
| `DEBUG` | `0` | Flask debug mode |
| `HOST` | `127.0.0.1` | Flask bind host |
| `PORT` | `5000` | Flask bind port |
| `SLOW_REQUEST_MS` | `3000` | Threshold for slow-request warnings (ms) |
| `INDEX_SYNC_INTERVAL_SEC` | `300` | Worker index sync interval |
| `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | fastembed model name |
| `PMA_NEWS_WATCH_CRON_DISABLED` | `` | Set to `1` to disable midnight news-watch submit |
| `CORS_ORIGINS` | — | Allowed CORS origins (production) |
| `TZ` | — | Container timezone (e.g. `Asia/Kolkata`) |

### Key Secrets (in `secrets_app.py`)

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `KEYCLOAK_REALM_URL` | Public realm URL (e.g. `https://sso.mspv.app/realms/Office`) |
| `KEYCLOAK_CLIENT_ID` | Keycloak client ID (`pma`) |
| `KEYCLOAK_HOST_IP` | LAN IP of Keycloak host for internal URL rewrite |
| `O365_TENANT_ID` | Azure AD tenant ID |
| `O365_CLIENT_ID` | Azure app client ID |
| `O365_CLIENT_SECRET` | Azure app client secret |
| `O365_MAILBOX` | Sender mailbox UPN |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token |
| `TELEGRAM_DEFAULT_CHAT_ID` | Default Telegram chat ID |
| `JIRA_BASE_URL` | Jira instance URL |
| `JIRA_USER_EMAIL` | Jira API token owner email |
| `JIRA_API_TOKEN` | Jira API token |
| `MCP_API_KEY` | API key for external MCP clients |
| `MCP_USER` | Username mapped to MCP API key |
| `MCP_OAUTH_CLIENT_ID` | OAuth client ID for Claude.ai connectors |
| `MCP_OAUTH_CLIENT_SECRET` | OAuth client secret for Claude.ai connectors |

---

## 10. Development Workflow

### Running Locally

```bash
# Backend (from code/ directory, app venv)
uv run python -m backend.app
# → http://127.0.0.1:5000

# Frontend (from code/frontend/)
npm run dev
# → http://localhost:5173 (proxies /api to :5000)

# Worker (separate terminal, from code/)
uv run python -m backend.worker

# Tests (from tooling/ directory, tooling venv)
uv run pytest
```

### Auth in Development

Set `DEV_AUTH_BYPASS=1` in a `.env` file in `code/`. The backend will synthesise a `CurrentUser` for `DEV_USER` (default: `kla`) with admin role. No Keycloak server needed.

### Building for Production

```bash
# From tooling/build/
build.bat   # Windows — builds both images and pushes to registry
```

### Deploying to Production

```bash
# From tooling/ (tooling venv)
python prod-img-update.py   # SSH to prod server, docker compose pull + restart
```

### Syncing Production Data Locally

```bash
# From tooling/ (tooling venv)
python sync-prod-data.py    # rsync md corpus from prod to local data/
```

### Release / Version Bump

```bash
# From tooling/common/
python bump_ver.py patch    # bumps VERSION file: 0.1.41 → 0.1.42
python release.py           # tags + triggers build pipeline
```
