# SLEEPY — Setup Guide

> **Single-user personal project management assistant.**
> Zero to a working local dev instance, then the production deploy that is actually
> live today: Docker Compose on an AWS EC2 host behind the host's own nginx, with
> SLEEPY's built-in username/password login. (Keycloak, Telegram, WhatsApp and Jira
> were all removed — if you see them mentioned anywhere else, that doc is stale.)

---

## Prerequisites

| Tool | Min version | Install |
|---|---|---|
| Python | 3.12 | via uv (see below) |
| Node.js | 18 | nodejs.org |
| uv | latest | `pip install uv` or `winget install astral-sh.uv` |
| Git | any | git-scm.com |
| Docker Desktop | any | docker.com — only for the optional dev Chroma container and for prod |

---

## Part 1 — First-Time Local Setup

### 1.1 Clone and install dependencies

```bat
git clone <repo-url>
cd SLEEPY
uv sync
cd code\frontend && npm install && cd ..\..
```

### 1.2 Create `secrets_app.py`

This is the only file you ever edit for secrets. It is gitignored and excluded from the
Docker build context (`.dockerignore`).

```bat
copy code\backend\example_secrets_app.py code\backend\secrets_app.py
```

Open `code\backend\secrets_app.py` and set at minimum:

```python
ANTHROPIC_API_KEY = "sk-ant-api03-..."   # console.anthropic.com → API Keys
```

That one key drives every LLM path: chat, morning briefing, deadline planning, news
watch. There is no other credential source — the old fallback that borrowed the Claude
Code OAuth token from `~/.claude/.credentials.json` has been removed.

`AUTH_SECRET_KEY` can stay blank for local dev because `tooling\run-backend.bat` sets
`DEV_AUTH_BYPASS=1` (no login screen, synthetic admin user). The moment you run with
bypass off, `config.py` refuses to start unless `AUTH_SECRET_KEY` is set and at least
32 characters.

### 1.3 Create the data directory

```bat
mkdir data\klm\db\sqlite
mkdir data\klm\db\chroma
cd data\klm
git init
git commit --allow-empty -m "init corpus"
cd ..\..
```

Seed the two mandatory root files:

```bat
echo # Inbox > data\klm\inbox.md
echo # About > data\klm\ABOUT.md
```

`data/klm/` is the MD corpus and its own git repo (the AI commits into it as
`sleepy <sleepy@smtw.in>`). `db/` inside it is derived app state (SQLite,
Chroma, news-watch state) and must **never** be committed — `md_editor.py` writes a
`.gitignore` there automatically on the first commit, but if you ever create the
repo by hand on a new machine, check it exists:

```
db/
*.db
*.db-shm
*.db-wal
*.sqlite3
.sleepy-git.lock
```

The SQLite file holds password hashes and every AI chat transcript, so a corpus repo
that tracks it must not be pushed anywhere.

### 1.4 Start the dev stack

```bat
tooling\run-backend.bat
```

That runs `main.py`, which starts Flask on `http://localhost:5000`, the Vite dev
server on `http://localhost:5173`, and the background worker (APScheduler + task
queue) as separate processes. Without the worker, nothing nightly ever fires.

Open `http://localhost:5173`. AI chat at `/ai` works as soon as the API key is set.

---

## Part 2 — ChromaDB in dev (semantic search context)

By default dev uses Chroma's embedded `PersistentClient` at `data/klm/db/chroma`
(`CHROMA_HOST` blank) — no Docker needed. Index the corpus once:

```bat
tooling\run-md-index.bat
```

The worker then re-indexes changed files every 5 minutes.

**Known limitation:** `main.py` runs Flask and the worker as two OS processes that
both open the same embedded Chroma directory. Chroma documents this as unsupported.
It works in practice for a single user, but if you ever see index corruption, switch
dev to the containerised server instead:

```bat
docker compose -f docker-compose.dev.yml up -d
```

and set in `secrets_app.py`:

```python
CHROMA_HOST = "localhost"
CHROMA_PORT = 8001
```

---

## Part 3 — Office 365 Email (the only integration)

Used for the morning briefing + deadline digest email and the `send_email` chat tool
(recipients restricted to `USER_EMAIL` / `@smtw.in`).

### 3.1 Azure app registration

1. [portal.azure.com](https://portal.azure.com) → **Microsoft Entra ID** → **App registrations** → **New registration**
2. Name `PMA Bot`, **Single tenant** → Register
3. Copy **Application (client) ID** → `O365_CLIENT_ID`, **Directory (tenant) ID** → `O365_TENANT_ID`

### 3.2 Grant `Mail.Send`

**API permissions** → **Add a permission** → **Microsoft Graph** → **Application
permissions** → `Mail.Send` → Add → **Grant admin consent**.

### 3.3 Client secret

**Certificates & secrets** → **New client secret** → copy the *Value* immediately →
`O365_CLIENT_SECRET`.

### 3.4 Fill in `secrets_app.py`

```python
O365_TENANT_ID     = "..."
O365_CLIENT_ID     = "..."
O365_CLIENT_SECRET = "..."
O365_MAILBOX       = "pmabot@smtw.in"   # real mailbox the bot sends from
O365_SENDER_NAME   = "PMA Bot"
USER_EMAIL         = "klm@smtw.in"      # where the nightly digest goes
```

---

## Part 4 — Production Deploy (AWS EC2, host nginx)

Live URL: `https://klm.smtw.in`. TLS is terminated by the host's own nginx + certbot;
the `backend` and `frontend` containers publish to `127.0.0.1` only.
`tooling/aws-ssh.sh` opens a shell on the box.

### 4.1 On the host — first time only

```bash
git clone <repo-url> /opt/sleepy && cd /opt/sleepy
cp code/backend/example_secrets_app.py code/backend/secrets_app.py
nano code/backend/secrets_app.py     # ANTHROPIC_API_KEY, AUTH_SECRET_KEY, O365_*, USER_EMAIL
chmod 600 code/backend/secrets_app.py

# Generate the signing key (must be >= 32 chars; config.py refuses shorter)
python3 -c "import secrets; print(secrets.token_hex(32))"

mkdir -p data/klm/db/sqlite data/klm/db/chroma
cd data/klm && git init && git commit --allow-empty -m "init corpus" && cd ../..
sudo chown -R 1000:1000 data     # containers run as uid 1000
```

`docker-compose.yml` sets `APP_ENV=production`, `DEV_AUTH_BYPASS=0`,
`TZ=Asia/Kolkata`, mounts `secrets_app.py` read-only at
`/app/code/backend/secrets_app.py`, and runs gunicorn with a threaded worker so a
long chat stream can't block `/healthz`.

### 4.2 Build and start

```bash
docker compose build
docker compose up -d
docker compose ps
curl -s http://127.0.0.1:5000/healthz
```

Services: `backend` (gunicorn :5000 → host 127.0.0.1:5000), `worker`, `frontend`
(nginx :80 → host 127.0.0.1:8080), `chromadb` (1.5.x, internal only).

### 4.3 Host nginx

Splice the two `server{}` blocks from `tooling/nginx-klm.smtw.in.conf` into
`/etc/nginx/nginx.conf` (keep the certbot-managed `ssl_*` lines as they are). Note the
`limit_req_zone` line must sit at `http{}` level — it throttles `/api/auth/login` to
10 requests/min per IP. Then `nginx -t && systemctl reload nginx`.

### 4.4 Create the login accounts

There is no signup UI. Provision from inside the backend container:

```bash
docker compose exec backend uv run python code/backend/manage_users.py create-user klm user
docker compose exec backend uv run python code/backend/manage_users.py create-user admin admin
docker compose exec backend uv run python code/backend/manage_users.py list-users
```

`user` = full daily-use access. `admin` = the same plus the **Security** view of login
attempts (IP, GeoIP location, device). Password minimum is 8 characters; pick long
random ones and keep them in a password manager — never in a file inside the repo.

Other commands: `reset-password <username>` (also signs out every existing session
for that user) and `delete-user <username>`.

### 4.5 Sessions and sign-out

Logins issue a 7-day HS256 JWT (`AUTH_TOKEN_TTL_DAYS`). Signing out, or a password
reset, bumps the user's `token_version` and immediately invalidates every token they
hold. Five failed attempts from one address for one username, or twenty from one
address across any usernames, lock that address out for 15 minutes.

### 4.6 Index the corpus

Trigger a full reindex from the UI (**Assistant** → "reindex"), or:

```bash
docker compose exec worker uv run python code/backend/md_indexer.py
```

### 4.7 Updating

```bash
cd /opt/sleepy && git pull
docker compose build && docker compose up -d
```

DB migrations apply automatically on start (`local_db._MIGRATIONS`, append-only).

---

## Part 5 — Nightly Schedule Reference (IST)

| Time | Job | What it does |
|---|---|---|
| 00:00 | `news_watch_submit` | Submits the news batch to the Anthropic Batches API |
| 00:05 | `materialise` | Recur → Plans + Daily + Govern (also runs once at every worker start) |
| 02:00 | `md_reindex` | Full ChromaDB re-index |
| 06:30 | `morning_briefing` | Deadline planning + briefing; emails the digest to `USER_EMAIL` |
| 23:00 | `housekeeping` | Corpus checks → inbox findings, archive old Daily files, prune old queue/login rows |
| every 5 min | `index_sync` | Incremental ChromaDB sync |
| every 5 min | `news_watch_finalize` | Polls the batch; writes surviving bullets to `inbox.md` |
| hourly | `commit_pending` | Auto-commits any uncommitted corpus changes |

A task that dies mid-run (worker restart) is reclaimed after 30 minutes and retried
with exponential backoff, up to 3 attempts.

---

## Quick Checklist

### Minimum (AI chat working locally)

- [ ] `uv sync` and `npm install` done
- [ ] `secrets_app.py` created with `ANTHROPIC_API_KEY` set
- [ ] `data/klm/` exists with `inbox.md`, `ABOUT.md`, and is a git repo
- [ ] `tooling\run-backend.bat` starts Flask + Vite + worker without errors
- [ ] AI chat at `/ai` responds

### Email working

- [ ] Azure app registration with `Mail.Send` admin-consented
- [ ] `O365_*` and `USER_EMAIL` filled in

### Production live

- [ ] `AUTH_SECRET_KEY` set (>= 32 chars), `secrets_app.py` is `chmod 600`
- [ ] `docker compose up -d` — all containers healthy
- [ ] host nginx reloaded with the `klm.smtw.in` blocks + `limit_req_zone`
- [ ] `https://klm.smtw.in/healthz` returns `{"status": "ok"}`
- [ ] `klm` (user) and `admin` (admin) accounts created via `manage_users.py`
- [ ] Corpus indexed
- [ ] `data/klm/.gitignore` ignores `db/` and `git -C data/klm ls-files db` prints nothing

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Backend exits at start with `AUTH_SECRET_KEY` message | bypass off but key blank/short | Generate a 64-hex key, set it, restart |
| Backend exits with `ANTHROPIC_API_KEY` message in prod | key missing from `secrets_app.py` or file mounted at the wrong path | Check the volume line in `docker-compose.yml` points at `/app/code/backend/secrets_app.py` |
| AI chat shows "Internal error — check server logs" | `ANTHROPIC_API_KEY` empty or invalid | Set it, restart |
| AI has no project context | Corpus not indexed, or Chroma server/client version mismatch | Run the reindex; in prod make sure the `chromadb/chroma` image tag is 1.5.x to match the Python client |
| Apply on an AI edit returns 409 "changed on disk" | The file was modified after the diff was proposed (news watch, capture, another edit) | Discard and ask again; the new diff is computed against the current file |
| "Too many failed attempts" on login | Lockout: 5 failures/15 min per (user, IP), 20 per IP | Wait 15 minutes, or reset the password from the CLI |
| Nightly jobs fire at odd hours | Container clock is UTC | Confirm `TZ=Asia/Kolkata` on both `backend` and `worker` |
| Email fails silently | `Mail.Send` not consented or client secret expired | Re-grant consent / rotate the secret in Azure |
| Run-md-index / worker git errors mentioning `index.lock` | Two processes committing at once (should no longer happen — every commit path takes `.sleepy-git.lock`) | Remove a stale `data/klm/.git/index.lock` if the process that held it is gone |
