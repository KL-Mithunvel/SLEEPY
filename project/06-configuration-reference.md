# Configuration Reference

## Overview

PMA uses a three-tier configuration system that merges values from three sources, checked in priority order:

1. **Environment variables** — highest priority, always wins
2. **`secrets_app.py`** — optional Python file mounted read-only in Docker; never committed to git
3. **Default value** — coded default, used when neither of the above provides a value

The merge logic lives in `config.py` via the `_get(key, default)` function:

```python
def _get(key: str, default=None):
    """Env wins, then secrets_app.py, then default. Values keep their original type."""
    if key in os.environ:
        return os.environ[key]
    if secrets_app is not None and hasattr(secrets_app, key):
        return getattr(secrets_app, key)
    return default
```

**Important:** Environment variables are always strings. When `_get` returns an env var value, it is a string even if the `secrets_app.py` value would have been an `int` or `bool`. Callers that need a typed value must cast explicitly (e.g. `int(_get(...))`, `str(_get(...)).strip() in ("1", "true", "yes")`).

`python-dotenv` is loaded at import time, so a `.env` file at the working directory is also picked up — useful in local dev without `secrets_app.py`.

---

## The `secrets_app.py` File

This file is the primary secrets carrier in production. It is a plain Python module:

```python
# secrets_app.py — NOT committed to git. Mount :ro in Docker at runtime.
# Copy from example_secrets_app.py and fill in.

KEYCLOAK_REALM_URL = "https://sso.mspv.app/realms/Office"
KEYCLOAK_CLIENT_ID = "pma"
KEYCLOAK_HOST_IP = "10.24.0.18"   # empty string to use public URL

LLM_PROVIDER = "anthropic"
ANTHROPIC_API_KEY = "sk-ant-..."

O365_TENANT_ID = ""
O365_CLIENT_ID = ""
O365_CLIENT_SECRET = ""
O365_MAILBOX = "user@smtw.in"
O365_SENDER_NAME = "Arivu Baalan BOT"

TELEGRAM_BOT_TOKEN = ""
TELEGRAM_DEFAULT_CHAT_ID = ""

JIRA_BASE_URL = "https://smtw-jira.atlassian.net"
JIRA_USER_EMAIL = ""
JIRA_API_TOKEN = ""

MCP_API_KEY = ""
MCP_USER = "kla"
MCP_OAUTH_CLIENT_ID = "pma-mcp"
MCP_OAUTH_CLIENT_SECRET = ""

INDEX_SYNC_INTERVAL_SEC = 300
```

**Security rules:**
- `secrets_app.py` is gitignored. It must never be committed.
- The companion `example_secrets_app.py` IS committed with placeholder values and comments. It is the template developers copy.
- The file is named `secrets_app.py`, NOT `secrets.py` — the name `secrets` shadows a Python stdlib module.
- In Docker, mount as `:ro` at `/app/backend/secrets_app.py`.
- All reads go through `config.py`. Application modules import from `config`, never from `secrets_app` directly. This keeps all secret access auditable at one location.

---

## All Configuration Variables

### Core / Filesystem

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DATA_ROOT` | `Path` | `<repo-root>/data/` | Base directory for all user data. Each user gets `DATA_ROOT/<username>/`. In production set to `/data` (Docker volume mount point). |
| `DEBUG` | `bool` | `False` | Flask debug mode. Never enable in production. Read from `DEBUG` env var only (not via `_get`). |
| `HOST` | `str` | `"127.0.0.1"` | Flask bind host. |
| `PORT` | `int` | `5000` | Flask bind port. |
| `SLOW_REQUEST_MS` | `int` | `3000` | Requests slower than this (milliseconds) are logged as warnings. |
| `DEV_USER` | `str` | `"kla"` | Fallback username when auth is bypassed (development only). |

### Authentication

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DEV_AUTH_BYPASS` | `bool` | `False` | When `"1"`, skip Keycloak token validation entirely. The `X-Dev-User` header (or `DEV_USER`) is used as the username. **Never enable in production.** Read from `DEV_AUTH_BYPASS` env var. |
| `KEYCLOAK_REALM_URL` | `str` | required | Full realm URL including the realm name. Example: `https://sso.mspv.app/realms/Office`. Used for issuer validation and JWKS endpoint construction. |
| `KEYCLOAK_CLIENT_ID` | `str` | `"pma"` | Keycloak client ID. Used to validate the `azp` claim in JWT tokens. |
| `KEYCLOAK_HOST_IP` | `str` | `""` (empty) | LAN IP of the Keycloak host. When set, the JWKS endpoint fetch is rewritten from the public HTTPS URL to `http://<IP>:8080/...` for container-to-Keycloak direct access (bypasses the reverse proxy). The public URL is still used for issuer validation. Empty string = use public URL for everything (development or when Keycloak is directly accessible). |

### AI / LLM

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ANTHROPIC_API_KEY` | `str` | required | Anthropic API key for Claude. Used by LiteLLM when `LLM_PROVIDER = "anthropic"`. |
| `LLM_PROVIDER` | `str` | `"anthropic"` | LLM provider selection. Only `"anthropic"` is supported in the current implementation. |

### Embedding Model

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `EMBED_MODEL` | `str` | `"BAAI/bge-small-en-v1.5"` | FastEmbed/ONNX model name for local embedding generation. The model is downloaded on first use (~100 MB) and cached. No API call needed. Read from `EMBED_MODEL` env var directly. |

### Email (Office 365 Graph API)

All O365 variables use the client-credentials OAuth flow — the app authenticates as itself, not as a user. The mailbox is a shared mailbox or user mailbox the bot sends from.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `O365_TENANT_ID` | `str` | `""` | Azure Active Directory tenant ID. |
| `O365_CLIENT_ID` | `str` | `""` | Azure app registration client ID. |
| `O365_CLIENT_SECRET` | `str` | `""` | Azure app registration client secret. |
| `O365_MAILBOX` | `str` | `""` | The mailbox address the bot sends from (e.g. `arivu@smtw.in`). |
| `O365_SENDER_NAME` | `str` | `"Arivu Baalan BOT"` | Display name in the `From:` field of outgoing emails. |

When any of `O365_TENANT_ID`, `O365_CLIENT_ID`, or `O365_CLIENT_SECRET` is empty, email functionality is disabled. The email service checks for this at runtime.

### Telegram

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | `str` | `""` | Bot token from @BotFather. Empty string disables Telegram notifications. |
| `TELEGRAM_DEFAULT_CHAT_ID` | `str` | `""` | Fallback group or chat ID to send messages to when no specific chat is specified. |

### Jira Cloud API

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `JIRA_BASE_URL` | `str` | `""` | Jira Cloud base URL. Example: `https://smtw-jira.atlassian.net`. Empty string disables Jira sync. |
| `JIRA_USER_EMAIL` | `str` | `""` | Email address of the Jira account that owns the API token. |
| `JIRA_API_TOKEN` | `str` | `""` | Jira API token from `id.atlassian.com`. |

The Jira service uses Basic auth with `JIRA_USER_EMAIL:JIRA_API_TOKEN`. PMA pulls from Jira (read-only for task sync) — it never writes to Jira.

### MCP Server

The MCP server exposes PMA tools to external LLM clients (Claude Desktop, Claude.ai connectors, etc.).

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MCP_API_KEY` | `str` | `""` | Static API key for simple MCP client authentication. Clients send via `X-API-Key` header. Empty string disables simple API key auth. |
| `MCP_USER` | `str` | `"kla"` (= `DEV_USER`) | Username mapped to the `MCP_API_KEY`. MCP edits are committed with this username and the `mcp@smtw.in` author email. |
| `MCP_OAUTH_CLIENT_ID` | `str` | `"pma-mcp"` | OAuth 2.0 client_id for Claude.ai connector OAuth flow. |
| `MCP_OAUTH_CLIENT_SECRET` | `str` | `""` | OAuth 2.0 client_secret. Set a strong random value in production. |

### Worker / Indexing

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `INDEX_SYNC_INTERVAL_SEC` | `int` | `300` | How often (seconds) the worker syncs the ChromaDB index against MD files on disk. Default is 5 minutes. |

### News Watch

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `PMA_NEWS_WATCH_CRON_DISABLED` | `bool` | `False` | When `"1"`, the worker does NOT register the midnight news watch submit cron. Manual triggers from Settings UI still work. The 5-minute finalize poll still runs. Set to `1` in development to avoid accidental API usage. |
| `PMA_NEWS_RUN_ALL` | `bool` | `False` | When `"1"`, bypass the day-of-week filter and run news watch for all active projects regardless of day. |
| `PMA_NEWS_CALL_DELAY_S` | `float` | `0` | Seconds to sleep between news watch API calls. `0` = let the SDK handle backoff automatically. |
| `PMA_NEWS_MAX_RETRIES` | `int` | `8` | Maximum retries for news watch API calls on 429 (rate limited). |

---

## Computed Paths (from `config.py`)

These paths are computed once at import time from `__file__` location. They work correctly in both development (repo checkout) and in the Docker container (where `code/src` is copied to `/app/src`).

```python
# __file__ = code/backend/config.py
CODE_ROOT = Path(__file__).resolve().parents[1]   # code/
REPO_ROOT = CODE_ROOT.parent                        # repo root (development only)
SRC_ROOT = CODE_ROOT / "src"                        # code/src/
PROMPTS_DIR = SRC_ROOT / "prompts"                  # code/src/prompts/
SKILLS_DIR = PROMPTS_DIR / "skills"                 # code/src/prompts/skills/
HELP_ROOT = SRC_ROOT / "help"                       # code/src/help/
TEMPLATES_SEED_ROOT = SRC_ROOT / "templates" / "user"   # code/src/templates/user/
TEMPLATES_ROOT = TEMPLATES_SEED_ROOT / "md"             # code/src/templates/user/md/
EMBED_MODEL = "BAAI/bge-small-en-v1.5"             # (also overrideable via env)
```

In the Docker container, `COPY code/src ./src` puts all read-only resources at `/app/src/`, so `CODE_ROOT / "src"` resolves correctly as `/app/src/`.

---

## Per-User Path Convention (`CurrentUser` dataclass)

The `CurrentUser` frozen dataclass in `config.py` provides computed paths for a given authenticated user:

```python
@dataclass(frozen=True)
class CurrentUser:
    username: str         # Keycloak username (local part of email, e.g. "kla")
    email: str = ""       # Keycloak email claim

    @property
    def data_root(self) -> Path:
        return DATA_ROOT / (self.username or DEV_USER)

    @property
    def md_root(self) -> Path:
        return self.data_root / "md"

    @property
    def db_path(self) -> Path:
        return self.data_root / "db" / "pma.sqlite3"

    @property
    def v_db_path(self) -> Path:
        return self.data_root / "db" / "chroma"
```

The username is derived from the Keycloak token's `username` claim. The claim format is `<user>@office.smtw.in`; only the local part (before `@`) is used as the username key. This means `DATA_ROOT/kla/` for a user whose Keycloak `username` claim is `kla@office.smtw.in`.

---

## `code/src/` Directory Structure (Read-Only App Resources)

These files are shipped with the app and read by the AI at runtime via the `read_src` tool. They are not part of any user's MD corpus.

```
code/src/
  prompts/
    SystemPrompt.MD                  # hot-reloaded system prompt for AI chat (loaded per request)
    skills/
      daily-review.md                # morning daily review workflow
      email-triage.md                # email triage workflow
      meeting-prep.md                # meeting preparation workflow
      monthly-compliance.md          # monthly compliance tracking
      monthly-planning.md            # monthly planning workflow
      people-delegation.md           # delegation support workflow
      project-setup.md               # new project setup workflow
      quarterly-planning.md          # quarterly planning workflow
      weekly-review.md               # weekly review workflow
  templates/
    user/
      ABOUT.md                       # user profile template (seeded to data/<user>/ on onboarding)
      md/                            # MD corpus templates (LLM reads via read_src)
        <project-template>.md
        <daily-template>.md
        ...
      ExampleOU/                     # example OU structure shipped as starter corpus
        Projects/
        Daily/
        Plans/
        Recur/
  help/
    Playbook-Format.md               # playbook grammar reference (read by LLM at runtime)
    Recur-Format.md                  # recur file format reference (read by LLM at runtime)
    <other-help-docs>.md
```

### Skill Files

Each file in `code/src/prompts/skills/` defines a named skill that the AI can invoke. Skills use YAML frontmatter to declare their name and description:

```yaml
---
name: daily-review
description: Run the morning daily review — check calendar, scan overdue tasks, etc.
---
```

The AI skill system loads the appropriate skill file when the user invokes a skill by name or by trigger phrase. Skills define step-by-step workflows for complex multi-step tasks.

### `SystemPrompt.MD`

The system prompt is hot-reloaded from `code/src/prompts/SystemPrompt.MD` on each request. This means the system prompt can be updated without restarting the application — useful for tuning AI behaviour in production without a deployment.

---

## Docker Configuration

### Production `docker-compose.yml` Services

| Service     | Image / Source            | Role                                                                          |
|-------------|---------------------------|-------------------------------------------------------------------------------|
| `backend`   | Built from repo           | Flask + gunicorn. Handles auth, chat, MD patch flow, API routes.              |
| `worker`    | Same image, different cmd | APScheduler + task queue drainer. Runs completely separate from web process.  |
| `frontend`  | Built from repo           | Vue 3 SPA served as static files (or Nuxt node if SSR needed).               |
| `keycloak`  | Official Keycloak image   | OIDC/OAuth2 + Active Directory + Passkeys. Existing `Office` realm reused.    |
| `caddy`     | Official Caddy image      | HTTPS termination, Let's Encrypt certs, reverse proxy to backend.            |
| `chromadb`  | Official ChromaDB image   | Vector store for MD corpus. Persists to Docker volume.                       |

### Environment Variables in Docker Compose

```yaml
services:
  backend:
    environment:
      DATA_ROOT: /data
      CORS_ORIGINS: https://pma.mspv.app
      TZ: Asia/Kolkata
    volumes:
      - /path/to/secrets_app.py:/app/backend/secrets_app.py:ro
      - pma_data:/data

  worker:
    environment:
      DATA_ROOT: /data
      TZ: Asia/Kolkata
    volumes:
      - /path/to/secrets_app.py:/app/backend/secrets_app.py:ro
      - pma_data:/data
```

**Timezone:** `TZ: Asia/Kolkata` is set on containers that need IST for timestamps (backend, worker). Unlike the Project-Starter baseline (which avoids `TZ` on containers to keep log timezones aligned with the host), PMA explicitly sets `TZ=Asia/Kolkata` for the app containers because SQLite stores timestamps as IST strings and the containers need to produce consistent IST times.

**Data volume:** `pma_data` is a Docker named volume (or bind mount) shared between `backend` and `worker`. This is where all user data lives (`/data/<username>/`).

### CORS Configuration

- `CORS_ORIGINS` env var — comma-separated list of allowed origins for Flask-CORS.
- Production value: `https://pma.mspv.app`.
- Development: typically `http://localhost:5173` (Vite dev server) or `*` with `DEV_AUTH_BYPASS=1`.

---

## Gunicorn Configuration

Located at `tooling/build/src/gunicorn.conf.py`:

- **Worker class**: `sync` (or `gthread` if thread-based concurrency is needed for long AI calls)
- **Workers**: 2–4 (single-server deployment)
- **Port**: 5000 (internal; Caddy proxies externally)
- **Timeout**: Long enough for AI chat requests (Claude API calls can take 30–60 seconds for complex prompts)

The `worker` service runs as a separate process (`python -m backend.worker`), completely separate from gunicorn. Gunicorn only runs the Flask web app; all background jobs, scheduling, and queue draining run in the worker process.

---

## Development Setup

### Prerequisites

- Python 3.12+ (`python3 --version`)
- Node.js 18+ (`node --version`)
- `uv` package manager (`pip install uv`)
- Keycloak running locally, OR set `DEV_AUTH_BYPASS=1` to skip auth entirely

### Backend Development Setup

```bash
cd /path/to/repo/code/

# Create the app venv and install all dependencies
uv sync

# Create secrets_app.py from the template
cp backend/example_secrets_app.py backend/secrets_app.py
# Edit backend/secrets_app.py and fill in at minimum ANTHROPIC_API_KEY

# Run Flask dev server with auth bypass
DEV_AUTH_BYPASS=1 DEV_USER=kla uv run flask --app backend.app run --debug
# Flask available at http://127.0.0.1:5000
```

With `DEV_AUTH_BYPASS=1`, the app synthesises a `g.user` with the username from the `X-Dev-User` header (defaulting to `DEV_USER`). No Keycloak token is required.

### Frontend Development Setup

```bash
cd /path/to/repo/code/frontend/

npm install

# Start Vite dev server
npm run dev
# Frontend available at http://localhost:5173
# Vite proxies /api/* → http://127.0.0.1:5000/api/*
```

The Vite proxy configuration means the frontend dev server transparently forwards all `/api/` requests to the Flask backend. The frontend never needs to know the backend's port in development.

### Worker Development Setup

```bash
cd /path/to/repo/code/

# Run the worker process (needs same secrets_app.py as backend)
DEV_AUTH_BYPASS=1 DEV_USER=kla uv run python -m backend.worker
```

The worker uses APScheduler and runs continuously. Jobs:
- `index_sync_job`: every `INDEX_SYNC_INTERVAL_SEC` seconds (default 5 min) — syncs ChromaDB with MD files
- `materialise_job`: daily at midnight — seeds daily files, manifests recurring tasks into plans
- `commit_pending_job`: periodic batch commits of uncommitted user edits
- `news_watch_submit_job`: nightly — submits batch news watch requests (disabled if `PMA_NEWS_WATCH_CRON_DISABLED=1`)
- `news_watch_finalize_job`: every 5 minutes — polls for completed news watch batch results
- `jira_sync_job`: periodic — syncs Jira issues to SQLite snapshot
- `queue_drain_job`: every 15 seconds — processes pending email and Telegram task queue items

### Tooling Venv Setup

The `tooling/` directory has its own separate `uv` project and venv. It must stay strictly isolated from the app venv.

```bash
cd /path/to/repo/tooling/

uv sync
# Creates tooling/.venv/ with dev scripts only (bump_ver, release, sync_common, etc.)
```

**Isolation rule:** Scripts in `tooling/` must NOT import from `code/`. App code must NOT import from `tooling/`. The tooling venv includes `python-dotenv` for reading `.env` files in scripts.

---

## Authentication Flow (Keycloak + PKCE)

### Token Validation (Backend)

The backend validates Keycloak JWT tokens on every request:

1. Extract `Bearer` token from the `Authorization` header.
2. Use `PyJWT`'s `PyJWKClient` to fetch signing keys from the JWKS endpoint.
3. If `KEYCLOAK_HOST_IP` is set, rewrite the JWKS endpoint URL from the public HTTPS URL to the internal LAN HTTP URL (e.g. `https://sso.mspv.app/realms/Office` → `http://10.24.0.18:8080/realms/Office`). This allows container-to-Keycloak access without going through the reverse proxy.
4. Decode with `algorithms=["RS256"]` and `verify_aud=False` (Keycloak sets `aud="account"` which doesn't match the client ID).
5. Validate `azp` claim matches `KEYCLOAK_CLIENT_ID`.
6. On decode failure (e.g. key rotation), evict the cached JWKS client and retry once.
7. Extract roles from both `realm_access.roles` and `resource_access.<client_id>.roles`.
8. Extract `username` claim (format: `user@domain`) and split to get the local username part.
9. Populate `g.user` and construct `CurrentUser` struct for the request.

### Dev Auth Bypass

When `DEV_AUTH_BYPASS=1`:
- The `before_request` handler skips all Keycloak validation.
- `g.user` is synthesised with `username` from `X-Dev-User` header (or `DEV_USER` if header absent).
- All permission checks pass (user gets `admin` role).
- Log messages confirm bypass is active.

### Frontend OAuth (PKCE S256)

The Nuxt/Vue frontend uses the Keycloak JS adapter:

1. App loads → fetches Keycloak config from `/api/auth/config` (public endpoint).
2. Initialises Keycloak with `login-required` flow and PKCE `S256` challenge method.
3. On successful authentication, calls `/api/auth/me` with the `Bearer` token.
4. Stores `role` and `permissions` from `/api/auth/me` response in the auth store.
5. Injects `Bearer` token into all subsequent API requests via the `api.js` helper.

The frontend never parses the Keycloak JWT itself — all user/role information comes from the `/api/auth/me` backend endpoint.

---

## RBAC Configuration

PMA uses the RBAC pattern from Project-Starter, adapted for single-user operation with forward compatibility for multi-user.

### `config_rbac.py`

Single source of truth for the access control policy:

```python
ROLES = ("admin", "owner")   # ordered most → least privileged

PERMISSIONS = {
    # Each key is 'module:action'
    # Each value is a tuple of role names that have this permission
    # admin bypasses all checks unconditionally (never listed here)
    "projects:read":      ("owner",),
    "projects:write":     ("owner",),
    "daily:read":         ("owner",),
    "daily:write":        ("owner",),
    "settings:read":      ("owner",),
    "settings:write":     ("owner",),
    # ... etc
}
```

For v1 (single user), there is effectively one role (`owner`) that has all permissions. The RBAC skeleton exists to make future multi-user addition trivial without a rewrite.

### `auth_utils.py` Public Functions

- `compute_permissions(roles)` — returns the union of all permission keys for the given list of roles. Used on login to build the user's permission set.
- `has_perm(perm_key)` — boolean check; uses `g.user["permissions"]`. For inline field masking inside a route.
- `require_perm(perm_key)` — route decorator; returns 403 if the current user lacks the permission.

Admin users bypass all `require_perm` checks unconditionally in code — `admin` is never listed in PERMISSIONS tuples.

---

## Database Configuration

### SQLite (Application State)

SQLite is used for application state: sessions, task queue, job history, AI interaction logs, Jira issue snapshots.

- **Path**: `DATA_ROOT/<username>/db/pma.sqlite3`
- **Connection**: opened per-request, cached on `g.db`, returned to pool on teardown
- **Migration pattern**: append-only ordered `_MIGRATIONS` list in `local_db.py`. `init_db()` runs at backend startup. Single-writer convention (backend owns schema; worker never migrates). `CREATE TABLE IF NOT EXISTS` and `ADD COLUMN IF NOT EXISTS` for defensive migrations.
- **Timezone**: IST. SQLite stores timestamps as ISO-8601 strings with IST offset. Python `datetime` objects are formatted as IST strings before storage.

The SQLite database is derived state — it can be deleted and rebuilt. The only non-rebuildable content is AI interaction history (`ai_events` table), which is an immutable event log.

### ChromaDB (Vector Index)

ChromaDB is used for semantic search over the MD corpus.

- **Persist path**: `DATA_ROOT/<username>/db/chroma/`
- **Collection name**: `md_corpus`
- **Client**: `chromadb.PersistentClient(path=str(v_db_path))`
- **Embed model**: `BAAI/bge-small-en-v1.5` via `FastEmbedEmbedding` (local ONNX, no API call)
- **Chunking**: `MarkdownNodeParser` (heading-aware chunking)
- **Metadata per chunk**: `ou`, `path`, `mtime`, `archived` (all string/float values)

ChromaDB is also derived state — deletable and rebuildable from the MD corpus via `IndexingService.rebuild_index()`.

---

## Task Queue Configuration

The DB-backed task queue runs in the worker process. No Redis or external queue service.

### SQLite Schema (sketch)

```sql
CREATE TABLE IF NOT EXISTS task_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    payload TEXT,                          -- JSON
    status TEXT DEFAULT 'pending',         -- pending|running|done|failed
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    scheduled_for TEXT DEFAULT (datetime('now', 'localtime')),
    locked_until TEXT,
    last_error TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_queue_status
    ON task_queue (status, scheduled_for)
    WHERE status = 'pending';
```

### Queue Drain Loop

The worker drains the queue every `QUEUE_DRAIN_INTERVAL_SEC` (15 seconds default):

1. `claim_next()` — SELECT + UPDATE with status=running + locked_until (SQLite single-writer is safe without FOR UPDATE)
2. Dispatch to handler via `task_type → handler` dispatch table in `task_handlers.py`
3. `mark_done()` or `mark_failed(error, retry_delay)` with exponential backoff

Task types include:
- `telegram_send` — send a Telegram message
- `email_send` — send an email via O365 Graph API
- `news_watch_submit` — submit a news watch batch for one project
- `news_watch_finalize` — poll and process completed news watch batch results
- `jira_sync` — sync Jira issues for one project

---

## API Conventions

### Response Format

- **Single object**: `jsonify(obj_dict)`
- **List**: `jsonify({'items': [...], 'total': N})` — always wrap, never return bare arrays
- **Paginated**: `jsonify({'items': [...], 'total': N, 'page': P, 'per_page': PP})`
- **Error**: `jsonify({'error': 'reason'}), <status_code>` — never leak stack traces to clients
- **Not found**: 404 with `{'error': '...'}`
- **Forbidden**: 403 via `require_perm` decorator
- **Bad request**: 400 with `{'error': 'reason'}`

### Date Format

ISO 8601 with IST offset: `2026-06-18T14:30:00+05:30`. Decimals are serialised as strings by Flask's `jsonify` (never `float()` monetary values on the backend).

### Slow Request Logger

Added to `app.py` from day one:

```python
@app.before_request
def _start_timer():
    g._req_start = time.monotonic()

@app.after_request
def _log_slow(response):
    elapsed_ms = int((time.monotonic() - g._req_start) * 1000)
    if elapsed_ms >= SLOW_REQUEST_MS:
        logger.warning("SLOW %d ms  %s %s  status=%s",
                       elapsed_ms, request.method, request.path, response.status_code)
    return response
```

---

## Testing Configuration

### Test Framework

- **Framework**: pytest
- **Location**: `code/tests/`
- **Venv**: `code/.venv/` (same as app; pytest is in app dependencies)
- **Run**: `cd code/ && uv run pytest`

### Testing Layers

**Layer 1 — Unit tests (no HTTP, no DB)**
- `config_rbac.py` structure: keys have colons, roles known, no admin in tuples
- `compute_permissions()`: admin gets all, each role gets expected subset
- `require_perm` decorator in isolation
- Pure functions: `materialiser.apply_plan_pipe()`, `materialiser.build_daily_content()`, `materialiser.build_plan_bullet()`, `md_patcher.extract_edit_blocks()`

**Layer 2 — HTTP enforcement tests**
- Role-specific test clients (fixtures in `conftest.py` with `DEV_AUTH_BYPASS=1`)
- Restricted roles → 403 on write operations
- Privileged roles are not blocked (may fail for other reasons, but not 403)

**Layer 3 — Integration tests**
- Flask test client with a temporary SQLite DB
- Test the MD patch flow end-to-end: submit `pma-edit` blocks → verify file written → verify git commit

**Rule:** Write `test_rbac.py` before any module-specific tests.

### `.bat` Wrappers for Claude Code

Thin `.bat` wrappers in `tooling/` reduce permission-prompt friction for Claude Code:

```
tooling/run-backend-tests.bat    →  cd code && uv run pytest
tooling/run-frontend-build.bat   →  cd code/frontend && npm run build
tooling/run-md-index.bat         →  cd code && uv run python -m backend.indexing_service
```

These are whitelisted in Claude Code permissions settings. Always use the wrappers, not direct `uv`/`npm` invocations, to keep Claude Code permission prompts minimal.

---

## Release Pipeline

### Version File

`VERSION` at the repo root. Semantic versioning: `MAJOR.MINOR.PATCH`. Managed exclusively by `tooling/common/bump_ver.py`.

### Release Script

`tooling/common/release.py` runs the full release pipeline:

1. Check current branch is `main` (override with `-B`)
2. Check clean working tree (no uncommitted changes)
3. Run tests via `TEST_CMD` from `tooling/common/release.env`
4. Bump version via `bump_ver.py` (`-p` = patch, `-m` = minor, `-M` = major)
5. Commit `VERSION` file + tag `vX.Y.Z`
6. Run build script (`BUILD_SCRIPT` from `release.env`) if set
7. Run deploy script (`DEPLOY_SCRIPT` from `release.env`) if set

Usage:
```bash
cd tooling/
uv run python common/release.py -p    # patch release
uv run python common/release.py -m    # minor release
```

### Shared Tooling Sync

`tooling/common/sync_common.py` pulls shared files from the `smtwkla/smtw-common` GitHub repo:

| Source file in smtw-common       | Destination in this repo         |
|-----------------------------------|----------------------------------|
| `claude-code/CLAUDE-COMMON.md`    | `.claude/CLAUDE-COMMON.md`       |
| `tooling/common/bump_ver.py`      | `tooling/common/bump_ver.py`     |
| `tooling/common/release.py`       | `tooling/common/release.py`      |

Requires a GitHub PAT in `tooling/common/.env-common` (gitignored).

---

## Production URL and Networking

- **Public URL**: `https://pma.mspv.app` (proxied via existing nginx + Certbot on `*.mspv.app`)
- **Caddy**: handles HTTPS termination and Let's Encrypt cert renewal inside the Docker compose stack
- **Keycloak**: `https://sso.mspv.app/realms/Office` — reuses the existing `Office` realm; PMA uses a `pma` public client with PKCE S256
- **Internal Keycloak access**: `http://10.24.0.18:8080` — backend accesses Keycloak directly by LAN IP for JWKS fetching, bypassing the reverse proxy

### Security Posture

- Keycloak-protected. No anonymous routes except `/healthz`.
- Passkey-first authentication; Active Directory-backed password fallback via existing Keycloak LDAP integration.
- `ANTHROPIC_API_KEY` and all other secrets in `secrets_app.py`, never sent to the frontend.
- AI calls redact `secrets_*.py` and `.env*` file content before including in any context.
- HTTPS-only via Caddy; HSTS enabled; mobile clients (iPhone primary use case) never see HTTP.
- Git push for MD repo is local-only — no remote. Git provides version history; VM infrastructure backup covers disaster recovery.
- No secrets in MD files — enforced by a pre-commit hook in the MD repo (planned).

---

## Common Pitfalls and Fixes

| Pitfall | Fix |
|---------|-----|
| `secrets.py` name shadows stdlib | Always name `secrets_app.py`, never `secrets.py` |
| Direct module import of `secrets_app` | All reads go through `config._get()` |
| `DEV_AUTH_BYPASS=1` accidentally in production | Guard with explicit production check in deployment scripts |
| `KEYCLOAK_HOST_IP` empty in container | Set to LAN IP so backend can reach Keycloak without reverse proxy |
| `EMBED_MODEL` download on first request in prod | Pre-warm the model cache during Docker image build or container startup |
| Two worker instances running simultaneously | Worker acquires a startup lock (file lock or SQLite advisory) to ensure single instance |
| ChromaDB `archived` filter type mismatch | `archived` is stored as string `"true"`/`"false"` — compare as strings, not booleans |
| `DATA_ROOT` not set in prod | Defaults to `<repo>/data/` — always set explicitly to `/data` in Docker environment |
| `TZ` policy | Set `TZ: Asia/Kolkata` on backend and worker containers; SQLite stores IST strings |
| Gunicorn timeout on long AI calls | Set gunicorn timeout > longest expected Claude API call (60–90s minimum) |
| Pool exhaustion on heavy load | `maxconn >= gunicorn_workers + worker_process + buffer`; monitor slow_request log |
| Frontend calling backend directly (not via proxy) | All API calls go through Vite proxy in dev; Caddy proxy in prod |
| Batch commit not running | Check worker is running; check `INDEX_SYNC_INTERVAL_SEC` is not set too high |
