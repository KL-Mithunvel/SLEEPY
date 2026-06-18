# PMA Dependencies Reference

## Backend Dependencies (`code/pyproject.toml`)

### Runtime Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `anthropic` | >=0.95.0 | Claude API client (chat, streaming, Message Batches) |
| `apscheduler` | >=3.11.2 | Background job scheduler for worker process |
| `chromadb` | >=1.5.7 | Embedded vector database for per-user corpus index |
| `fastembed` | >=0.8.0 | Local ONNX embedding inference (BAAI/bge-small-en-v1.5) |
| `flask` | >=3.1.3 | Web framework (factory pattern, blueprints) |
| `flask-cors` | >=6.0.2 | CORS middleware for frontend dev proxy |
| `gitpython` | >=3.1.46 | Git operations on MD corpus (commit, log, diff) |
| `jinja2` | >=3.1 | Template rendering (email templates, etc.) |
| `llama-index-core` | >=0.14.20 | RAG framework (MarkdownNodeParser, VectorStoreIndex) |
| `llama-index-embeddings-fastembed` | >=0.6.0 | fastembed adapter for LlamaIndex |
| `llama-index-readers-file` | >=0.6.0 | SimpleDirectoryReader for MD corpus |
| `llama-index-vector-stores-chroma` | >=0.5.5 | ChromaDB adapter for LlamaIndex |
| `msal` | >=1.36.0 | Microsoft Authentication Library (Office 365 email) |
| `pyjwt[crypto]` | >=2.12.1 | JWT validation with RS256 (Keycloak tokens) |
| `python-dotenv` | >=1.2.2 | `.env` file support in dev |
| `gunicorn` | >=21.0 | WSGI server for production |

### Dev Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | >=9.0.3 | Test runner |

## Frontend Dependencies (`code/frontend/package.json`)

### Runtime Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `vue` | ^3.4.0 | UI framework (Composition API) |
| `vue-router` | ^4.3.0 | Client-side routing (SPA) |
| `bootstrap` | ^5.3.3 | CSS framework + JS components |
| `bootstrap-icons` | ^1.11.3 | SVG icon library |
| `markdown-it` | ^14.1.1 | Markdown → HTML renderer |
| `keycloak-js` | ^26.2.3 | Keycloak OIDC client (PKCE S256) |

### Dev Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `@vitejs/plugin-vue` | ^6.0.0 | Vite plugin for Vue SFCs |
| `vite` | ^7.0.0 | Build tool and dev server |
| `vitest` | ^3.2.0 | Unit test framework (Vite-native) |

## Tooling Dependencies (`tooling/pyproject.toml`)

The tooling venv is SEPARATE from the app venv. Scripts in `tooling/` cannot import from `code/`.

| Package | Purpose |
|---------|---------|
| `python-dotenv` | `.env` file support in dev scripts |
| (other dev utilities) | sync scripts, release scripts, bump_ver |

## System Dependencies (Docker)

Installed in `Dockerfile.backend`:
- `git` — required by GitPython for MD corpus operations

## Pre-downloaded Model

The fastembed model is downloaded at Docker build time:
```dockerfile
RUN .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
```

- Model: `BAAI/bge-small-en-v1.5`
- Format: ONNX (runs CPU-only)
- Size: ~130MB
- Vector dimensions: 384
- Stored in: `~/.cache/fastembed/` (inside container, baked into image)

## Key Dependency Notes

### ChromaDB (>=1.5.7)
- Used as embedded vector store (no separate server)
- Persistent client: `chromadb.PersistentClient(path=str(v_db_path))`
- Per-user collection: `md_corpus`
- Metadata values must be strings/ints/floats (not booleans) — hence `archived: "true"/"false"` strings

### LlamaIndex
- 4 packages: core + embeddings-fastembed + readers-file + vector-stores-chroma
- Version 0.14.x (major API stable)
- `MarkdownNodeParser` splits documents on markdown headers
- `SimpleDirectoryReader` walks directory tree
- `VectorStoreIndex` manages embed-and-store pipeline

### Anthropic SDK (>=0.95.0)
- Used for: chat completions, streaming, Message Batches API
- Streaming: `anthropic.messages.stream()` context manager
- Prompt caching: `cache_control` parameter on content blocks
- Message Batches: async batch processing for news watch

### PyJWT[crypto] (>=2.12.1)
- `[crypto]` extra installs `cryptography` package
- Required for RS256 (RSA signature) JWT validation
- Keycloak issues RS256 tokens by default

### GitPython (>=3.1.46)
- Used for: `git add`, `git commit`, `git log`, `git show`
- Requires `git` binary installed (system package in Docker)
- Operations on `DATA_ROOT/<user>/md/` repository

### Flask-CORS (>=6.0.2)
- Handles CORS for API endpoints
- In development: allows requests from `http://localhost:5173` (Vite dev server)
- In production: allows `CORS_ORIGINS` env var (e.g. `https://pma.example.com`)

### APScheduler (>=3.11.2)
- `BackgroundScheduler` (thread-based)
- Job triggers: `IntervalTrigger` and `CronTrigger`
- Not distributed (single process, single machine)
- Jobs re-run automatically on schedule even if previous run fails

### MSAL (>=1.36.0)
- Microsoft Authentication Library
- Used for Office 365 email sending via Microsoft Graph API
- Client credentials flow with O365_CLIENT_ID + O365_CLIENT_SECRET + O365_TENANT_ID

## Python Version

- **Required**: Python 3.12+
- Enforced in `pyproject.toml`: `requires-python = ">=3.12"`
- Docker base image: `python:3.12-slim`

## Package Manager

- **Backend**: `uv` (not pip, not poetry)
  - `uv sync` — install from pyproject.toml
  - `uv sync --frozen` — install from uv.lock (reproducible)
  - `uv run <command>` — run in venv without activating
  - `uv add <package>` — add dependency (updates pyproject.toml + uv.lock)
- **Frontend**: `npm` (standard)

## Upgrade Considerations

When upgrading key dependencies:

| Dependency | Risk | Notes |
|-----------|------|-------|
| `anthropic` | Medium | API changes; check streaming event types |
| `chromadb` | High | Collection format may change; may need reindex |
| `llama-index-*` | High | Major API changes between versions |
| `keycloak-js` | Low | Check init() API changes |
| `flask` | Low | Stable API |
| `vue` | Low | Vue 3 stable |
| `vite` | Low | Build output compatible |
