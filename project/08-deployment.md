# PMA Deployment Guide

## Production Architecture Overview

```
Internet → Caddy (reverse proxy, TLS) → pma-frontend (Nginx, port 80)
                                      → pma-backend (gunicorn, port 5000)
                                      → pma-worker (APScheduler, no HTTP)
                     ↕
                  Keycloak SSO (sso.mspv.app, external)
```

## Docker Compose (Production)

File: `tooling/build/example-docker-compose.yml`

```yaml
# Production deployment template for PMA (pma.mspv.app).
# Copy to target server as docker-compose.yml and adjust secrets/volumes as needed.
#
# Required on host:
#   - REGISTRY image tags already pushed
#   - secrets_app.py mounted read-only into backend + worker
#   - DATA_ROOT directory on host holding per-user folders (data/<USER>/)

services:
  backend:
    image: REGISTRY/pma-backend:latest
    container_name: pma-backend
    restart: unless-stopped
    volumes:
      - ./secrets_app.py:/app/backend/secrets_app.py:ro
      - ${DATA_ROOT}:/data
    environment:
      DATA_ROOT: /data
      CORS_ORIGINS: https://pma.mspv.app
      TZ: Asia/Kolkata
    extra_hosts:
      - "sso.mspv.app:${KEYCLOAK_HOST_IP}"

  worker:
    image: REGISTRY/pma-backend:latest
    container_name: pma-worker
    command: [".venv/bin/python", "-m", "backend.worker"]
    restart: unless-stopped
    volumes:
      - ./secrets_app.py:/app/backend/secrets_app.py:ro
      - ${DATA_ROOT}:/data
    environment:
      DATA_ROOT: /data
      TZ: Asia/Kolkata
    extra_hosts:
      - "sso.mspv.app:${KEYCLOAK_HOST_IP}"

  frontend:
    image: REGISTRY/pma-frontend:latest
    container_name: pma-frontend
    restart: unless-stopped
    environment:
      TZ: Asia/Kolkata
    ports:
      - "80:80"
    depends_on:
      - backend
```

## Environment Variables for Docker Compose

Create a `.env` file alongside docker-compose.yml:
```bash
REGISTRY=your.registry.example.com
DATA_ROOT=/opt/pma/data
KEYCLOAK_HOST_IP=192.168.1.xxx  # internal IP of Keycloak server
```

## `secrets_app.py` (Host File, Mounted Read-Only)

```python
# /path/to/secrets_app.py — NEVER commit to git
ANTHROPIC_API_KEY = "sk-ant-api03-..."
KEYCLOAK_REALM_URL = "https://sso.mspv.app/realms/Office"
KEYCLOAK_HOST_IP = "192.168.1.xxx"
DATA_ROOT = "/data"
MCP_API_KEY = "your-mcp-api-key"

# Optional integrations:
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_CHAT_ID = "..."
O365_CLIENT_ID = "..."
O365_CLIENT_SECRET = "..."
O365_TENANT_ID = "..."
JIRA_URL = "https://company.atlassian.net"
JIRA_EMAIL = "user@company.com"
JIRA_TOKEN = "..."
JIRA_PROJECT_KEY = "PROJ"
```

## Backend Dockerfile (`tooling/build/Dockerfile.backend`)

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for dependency management
RUN pip install --no-cache-dir uv

# Python dependencies — install from lockfile for reproducible builds
COPY code/pyproject.toml code/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Pre-download fastembed model so first startup is fast
RUN .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

# Application code
COPY code/backend ./backend
# Read-only resources: prompts, templates, help docs
# Layout matches code/src/ in repo, so backend code paths resolve identically in dev and prod
COPY code/src ./src
COPY VERSION ./VERSION
COPY tooling/build/src/gunicorn.conf.py ./gunicorn.conf.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

EXPOSE 5000

# Run via the uv-managed venv
CMD [".venv/bin/gunicorn", "-c", "gunicorn.conf.py", "backend.app:app"]
```

### Key Dockerfile Details
- Base: `python:3.12-slim`
- `git` system package required: GitPython uses it for MD corpus operations
- `uv` installed globally (pip) then used to create `.venv/` from lockfile
- `uv sync --frozen --no-dev --no-install-project`:
  - `--frozen`: use exact versions from uv.lock
  - `--no-dev`: exclude dev dependencies
  - `--no-install-project`: don't install the project itself (just deps)
- fastembed model pre-downloaded at build time: `BAAI/bge-small-en-v1.5`
  - ONNX model, ~130MB
  - Pre-downloading avoids slow first startup in production
- `PYTHONPATH=/app`: allows `import backend.xxx` to work
- `CMD` uses `.venv/bin/gunicorn` (not system gunicorn)
- Worker container: same image, overrides CMD with `python -m backend.worker`

## Build Process

### Build Backend Image
```bash
# From repo root
docker build -f tooling/build/Dockerfile.backend -t REGISTRY/pma-backend:latest .
docker push REGISTRY/pma-backend:latest
```

### Build Frontend Image
```bash
cd code/frontend/
docker build -f Dockerfile.frontend -t REGISTRY/pma-frontend:latest .
docker push REGISTRY/pma-frontend:latest
```

### Tag with Version
```bash
VERSION=$(cat VERSION)
docker tag REGISTRY/pma-backend:latest REGISTRY/pma-backend:${VERSION}
docker tag REGISTRY/pma-frontend:latest REGISTRY/pma-frontend:${VERSION}
```

## Data Directory Setup (First Time)

```bash
# On production server
mkdir -p /opt/pma/data
# Data is auto-created per user on first login

# If you have existing MD corpus to import:
mkdir -p /opt/pma/data/<username>/md
cd /opt/pma/data/<username>/md
git init
# Copy existing MD files here
git add -A
git commit -m "initial: import corpus"

mkdir -p /opt/pma/data/<username>/db
# ChromaDB + SQLite will be auto-created on first use
```

## Caddy Configuration

PMA uses Caddy as the reverse proxy. Example `Caddyfile`:

```
pma.mspv.app {
    # Frontend (SPA)
    reverse_proxy /api/* pma-backend:5000
    reverse_proxy /mcp/* pma-backend:5000
    reverse_proxy /.well-known/* pma-backend:5000
    reverse_proxy /authorize pma-backend:5000
    reverse_proxy /token pma-backend:5000
    reverse_proxy /* pma-frontend:80

    # TLS auto-managed by Caddy (Let's Encrypt)
    tls admin@example.com
}
```

Note: Caddy handles TLS termination. Backend containers receive plain HTTP.

## Keycloak Setup

### Realm Configuration
- Realm name: `Office`
- Client ID: `pma`
- Client protocol: `openid-connect`
- Access type: `public` (no client secret, uses PKCE)
- Valid redirect URIs: `https://pma.mspv.app/*`
- Web origins: `https://pma.mspv.app`
- Standard flow: enabled
- Direct access grants: disabled (PKCE only)
- PKCE challenge method: `S256`

### User Setup
- Users are Keycloak users in the `Office` realm
- Username maps to `DATA_ROOT/<username>/` on disk
- Roles can be realm-level or client-level (both supported)

### JWKS URL (Backend Validation)
Backend fetches: `<KEYCLOAK_REALM_URL>/protocol/openid-connect/certs`

For containers using `extra_hosts`, this resolves via:
`https://sso.mspv.app/realms/Office` → internally: `http://<KEYCLOAK_HOST_IP>:8080/realms/Office`

## Production Startup Sequence

1. Start database services (if any external)
2. Start Keycloak
3. `docker compose up -d`
4. Verify:
   - `docker logs pma-backend` — should show gunicorn workers started
   - `docker logs pma-worker` — should show APScheduler jobs scheduled, initial index_sync running
   - `docker logs pma-frontend` — should show Nginx started
5. `curl https://pma.mspv.app/api/health` → `{"status": "ok", "version": "..."}`

## Upgrade Process

```bash
# Pull new images
docker compose pull

# Rolling restart (backend → worker → frontend)
docker compose up -d --no-deps backend
docker compose up -d --no-deps worker
docker compose up -d --no-deps frontend

# Verify health
curl https://pma.mspv.app/api/health
```

## Backup Strategy

1. **MD corpus**: already git-versioned in `DATA_ROOT/<user>/md/`. Push to remote git as backup:
   ```bash
   cd /opt/pma/data/<user>/md
   git remote add origin git@github.com:user/md-corpus.git
   git push -u origin main
   ```
2. **SQLite databases** (`pma.sqlite3`, `queue.sqlite3`): backup regularly
3. **ChromaDB**: can be rebuilt from MD corpus via `/api/corpus/reindex` — less critical to backup
4. **secrets_app.py**: store in a password manager or secrets vault

## Monitoring

- Health endpoint: `GET /api/health` → `{"status": "ok"}`
- Queue stats: `GET /api/corpus/queue-stats` (auth required)
- Index status: `GET /api/corpus/index-status` (auth required)
- Worker logs: `docker logs pma-worker`
- Slow request log: backend logs WARNING for requests >1000ms

## Scaling Considerations

- **Single user**: current architecture is designed for one user per PMA instance
- **Multiple users**: per-user data isolation is built in, but Keycloak user management needed
- **Backend workers**: gunicorn can run multiple workers (set in gunicorn.conf.py), but SQLite writer (task_queue) may be a bottleneck
- **ChromaDB**: embedded, not network-accessible — scales with single machine
- **Worker**: single APScheduler process; if multiple backend workers needed, worker should remain a single process

## gunicorn.conf.py (approximate)

```python
# tooling/build/src/gunicorn.conf.py
bind = "0.0.0.0:5000"
workers = 2
worker_class = "sync"
timeout = 120       # long enough for AI chat (streaming)
keepalive = 5
loglevel = "info"
accesslog = "-"     # stdout
errorlog = "-"      # stdout
```

## Version Management

- `VERSION` file at repo root contains semver string (e.g. `0.1.41`)
- `GET /api/health` returns this version
- Docker images tagged with version at build time
- Tooling scripts: `tooling/bump_ver.py` (or similar) for version bumping
