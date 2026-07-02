# SLEEPY — Setup Guide

> **Single-user personal project management assistant.**
> This guide covers every setup action needed from zero to a fully working local dev instance and production deploy.

---

## Prerequisites

| Tool | Min version | Install |
|---|---|---|
| Python | 3.12 | via uv (see below) |
| Node.js | 18 | nodejs.org |
| uv | latest | `pip install uv` or `winget install astral-sh.uv` |
| Docker Desktop | any | docker.com (only needed for ChromaDB and prod) |
| Git | any | git-scm.com |

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

This is the only file you ever need to edit for secrets. It is gitignored.

```bat
copy code\backend\example_secrets_app.py code\backend\secrets_app.py
```

Open `code\backend\secrets_app.py` and at minimum set:

```python
CLAUDE_API_KEY = "sk-ant-..."    # from console.anthropic.com → API Keys
```

Everything else can stay blank until you need that feature.

### 1.3 Create the data directory

```bat
mkdir data\kla\db\sqlite
mkdir data\kla\db\chroma
mkdir data\kla\logs
```

Create a bare git repo inside it (the AI commits edits here):

```bat
cd data\kla
git init
git commit --allow-empty -m "init corpus"
cd ..\..
```

Create the mandatory seed files:

```bat
echo # Inbox > data\kla\inbox.md
echo # About > data\kla\ABOUT.md
```

### 1.4 Start the dev server

```bat
tooling\run-backend.bat
```

Opens: `http://localhost:5173` (frontend) and `http://localhost:5000` (API).

AI chat at `/ai` will now work. Everything else (Telegram, email, WhatsApp) is optional.

---

## Part 2 — Anthropic API Key

1. Go to [console.anthropic.com](https://console.anthropic.com) → **API Keys**
2. Create a key, copy it
3. In `secrets_app.py`:

```python
CLAUDE_API_KEY = "sk-ant-api03-..."
```

4. Restart dev server.

**This is the only key required to use AI chat, morning briefing, and news watch.**

---

## Part 3 — Telegram Bot (inbound commands + push notifications)

### 3.1 Create the bot

1. Open Telegram → search **@BotFather** → `/start`
2. Send `/newbot`
3. Choose a name (e.g. `My PMA`) and username (e.g. `mypma_bot`)
4. Copy the token shown

### 3.2 Get your chat ID

1. Send any message to your new bot in Telegram
2. Open in a browser (replace `<TOKEN>` with your bot token):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. In the JSON response find `"chat": {"id": 123456789}` — copy that number

### 3.3 Generate a webhook secret

Run this once in any Python prompt to get a random secret:

```python
import secrets; print(secrets.token_hex(32))
```

### 3.4 Fill in `secrets_app.py`

```python
TELEGRAM_BOT_TOKEN       = "7123456789:AAFabc..."
TELEGRAM_DEFAULT_CHAT_ID = "123456789"
TELEGRAM_WEBHOOK_SECRET  = "the-random-hex-you-just-generated"

# Enable push notifications (morning briefing + news digest sent to Telegram)
NOTIFY_TELEGRAM = True
```

### 3.5 Register the inbound webhook (after prod deploy only)

Once the server is live at `https://pa.mspv.app`:

1. Open the app → **Integrations** → Telegram card → **Webhook** button
2. Enter: `https://pa.mspv.app/api/integrations/telegram/webhook`
3. Click **Register**

After this, messaging your bot triggers the AI. Commands:

| Command | What it does |
|---|---|
| `/briefing` | Returns today's morning briefing |
| `/tasks` | Lists open tasks from corpus |
| `/capture <text>` | Adds a bullet to `inbox.md` |
| `/help` | Shows all commands |
| Any other text | Free AI chat with corpus context |

> **Local dev note:** Telegram can't reach `localhost`. For local testing of the inbound bot, use [ngrok](https://ngrok.com): `ngrok http 5000` then register the ngrok URL as the webhook.

---

## Part 4 — WhatsApp (Meta Cloud API)

### 4.1 Meta Developer setup

1. Go to [developers.facebook.com](https://developers.facebook.com) → **My Apps** → **Create App**
2. Type: **Business** → Next
3. App name: `PMA Bot` → Create
4. On the dashboard, find **WhatsApp** → click **Set up**
5. Under **Getting Started**, copy the **Phone Number ID** → `WHATSAPP_PHONE_NUMBER_ID`

### 4.2 Create a permanent system user token

1. Go to [business.facebook.com](https://business.facebook.com) → **Settings** → **System users**
2. **Add** → name it `pma-bot`, role: Employee
3. Click **Add assets** → Apps → select your PMA Bot app → assign `Manage` permission
4. Click **Generate new token** → select the app → tick `whatsapp_business_messaging` → **Generate token**
5. **Copy immediately** (shown only once) → `WHATSAPP_TOKEN`

### 4.3 Fill in `secrets_app.py`

```python
WHATSAPP_PHONE_NUMBER_ID = "123456789012345"
WHATSAPP_TOKEN           = "EAAxxxxxxxx..."
WHATSAPP_DEFAULT_TO      = "+919876543210"    # your personal number, E.164 format

NOTIFY_WHATSAPP = True    # push morning briefing + news here too
```

### 4.4 Send a test message first

Meta requires a template message before free-form messages work to a new number. From the Meta Developer Console → WhatsApp → API Setup, send one "Hello World" template to your number. After that, the app can send free-form messages.

---

## Part 5 — Office 365 Email

### 5.1 Create an Azure app registration

1. [portal.azure.com](https://portal.azure.com) → **Azure Active Directory** → **App registrations** → **New registration**
2. Name: `PMA Bot`, Supported account types: **Single tenant** → Register
3. Copy **Application (client) ID** → `O365_CLIENT_ID`
4. Copy **Directory (tenant) ID** → `O365_TENANT_ID`

### 5.2 Grant Mail.Send permission

1. Left menu → **API permissions** → **Add a permission** → **Microsoft Graph**
2. Choose **Application permissions** → search `Mail.Send` → tick it → **Add permissions**
3. Click **Grant admin consent for [your org]** → Yes

### 5.3 Create a client secret

1. Left menu → **Certificates & secrets** → **New client secret**
2. Description: `pma-bot`, Expires: 24 months → Add
3. **Copy the Value immediately** (disappears after navigation) → `O365_CLIENT_SECRET`

### 5.4 Fill in `secrets_app.py`

```python
O365_TENANT_ID     = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
O365_CLIENT_ID     = "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy"
O365_CLIENT_SECRET = "the-secret-value-you-copied"
O365_MAILBOX       = "pmabot@smtw.in"    # must be a real mailbox in your tenant
O365_SENDER_NAME   = "PMA Bot"
```

---

## Part 6 — Jira Cloud (optional)

Only needed if you use Jira tickets. Enables the corpus sync (auto-ticking tasks when Jira issues are closed).

1. Go to [id.atlassian.com](https://id.atlassian.com) → **Security** → **API tokens** → **Create API token**
2. Name: `pma-bot` → Create → Copy the token

```python
JIRA_BASE_URL   = "https://yourco.atlassian.net"
JIRA_USER_EMAIL = "you@smtw.in"
JIRA_API_TOKEN  = "ATATxxxxxxxxxxxxxxx"
```

---

## Part 7 — ChromaDB (local dev — for AI search to work)

The AI uses ChromaDB to search your corpus. Without it, AI chat still works but has no project context.

```bat
docker compose -f docker-compose.dev.yml up -d
```

Then index your corpus:

```bat
tooling\run-md-index.bat
```

This only needs to be re-run when you add new markdown files outside the app (the worker auto-indexes every 5 minutes when running).

---

## Part 8 — Production Deploy (Proxmox VM)

### 8.1 On the VM — first time only

```bash
git clone <repo-url> /opt/sleepy
cd /opt/sleepy
cp code/backend/example_secrets_app.py code/backend/secrets_app.py
# Fill in ALL fields in secrets_app.py (see Parts 2–6 above)
nano code/backend/secrets_app.py

# Create the data directory (or restore from backup)
mkdir -p data/kla/db/sqlite data/kla/db/chroma data/kla/logs
cd data/kla && git init && git commit --allow-empty -m "init corpus" && cd ../..
```

### 8.2 Build and start

```bash
docker compose build
docker compose up -d
```

Services started: `backend` (gunicorn :5000), `worker` (APScheduler), `frontend` (nginx :80), `chromadb`, `caddy` (HTTPS :443).

Check health:

```bash
docker compose ps
curl https://pa.mspv.app/healthz
```

### 8.3 Keycloak — create the `pma` client

1. Open Keycloak admin → realm `Office.smtw.in` → **Clients** → **Create client**
2. **Client ID:** `pma` | **Client type:** OpenID Connect → Next
3. **Standard flow:** On | **Direct access grants:** Off | **Client authentication:** Off (public client) → Next
4. **Root URL:** `https://pa.mspv.app`
5. **Valid redirect URIs:** `https://pa.mspv.app/*`
6. **Web origins:** `https://pa.mspv.app` → Save
7. Go to **Users** → your user → **Role mapping** → Assign role `owner`

### 8.4 Register Telegram webhook

1. Open `https://pa.mspv.app` → **Integrations** → Telegram → **Webhook**
2. Enter: `https://pa.mspv.app/api/integrations/telegram/webhook`
3. Click **Register**

### 8.5 Index the corpus

```bash
docker compose exec worker uv run python code/backend/worker.py --once md_reindex
```

Or trigger from the UI: **Assistant** → type "reindex".

---

## Part 9 — Nightly Schedule Reference

| Time (IST) | Job | What it does |
|---|---|---|
| 00:00 | `news_watch_submit` | Submits news batch to Anthropic |
| 00:05 | `materialise` | Recur → Plans + Daily + Govern |
| 02:00 | `md_reindex` | Full ChromaDB re-index |
| 06:30 | `morning_briefing` | Generates briefing; pushes to Telegram/WhatsApp if configured |
| 07:00 | `due_reminders` | Scans corpus for due: dates; pushes reminders |
| 23:00 | `housekeeping` | Corpus health, inbox tidy, archive old daily logs |
| Every 5 min | `index_sync` | Incremental ChromaDB sync |
| Every 5 min | `news_watch_finalize` | Polls Anthropic batch; pushes news digest when ready |
| Every 15 min | `jira_sync` | Ticks closed Jira issues in corpus (if configured) |
| Every hour | `commit_pending` | Auto-commits any uncommitted corpus changes |

---

## Quick Checklist

### Minimum (AI chat working locally)

- [ ] `uv sync` and `npm install` done
- [ ] `secrets_app.py` created with `CLAUDE_API_KEY` set
- [ ] `data/kla/` directory created with `inbox.md` and `ABOUT.md`
- [ ] `data/kla/` is a git repo (`git init` inside it)
- [ ] `tooling\run-backend.bat` starts without errors
- [ ] AI chat at `/ai` responds

### Telegram working

- [ ] Bot created via @BotFather
- [ ] `TELEGRAM_BOT_TOKEN` and `TELEGRAM_DEFAULT_CHAT_ID` set
- [ ] `TELEGRAM_WEBHOOK_SECRET` set (random string)
- [ ] `NOTIFY_TELEGRAM = True` if you want push notifications
- [ ] Webhook registered via Integrations UI (after deploy)

### Email working

- [ ] Azure app registration created with `Mail.Send` permission
- [ ] `O365_*` fields all filled in `secrets_app.py`

### WhatsApp working

- [ ] Meta Developer app created with WhatsApp product
- [ ] System user token generated with `whatsapp_business_messaging` permission
- [ ] `WHATSAPP_*` fields filled in `secrets_app.py`
- [ ] One template message sent from Meta console to your number first

### Production live

- [ ] `docker compose build` succeeds
- [ ] `docker compose up -d` — all containers healthy
- [ ] `https://pa.mspv.app/healthz` returns `{"status": "ok"}`
- [ ] Keycloak `pma` client created, user assigned `owner` role
- [ ] Telegram webhook registered
- [ ] Corpus indexed (`md_reindex` task triggered)

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| AI chat shows "Internal error — check server logs" | `CLAUDE_API_KEY` is empty | Set it in `secrets_app.py`, restart server |
| AI has no project context | ChromaDB not running or corpus not indexed | `docker compose -f docker-compose.dev.yml up -d` then `tooling\run-md-index.bat` |
| Telegram bot doesn't reply | Webhook not registered or wrong secret | Re-register webhook via Integrations UI |
| Email fails silently | `Mail.Send` permission not granted or secret expired | Check Azure portal, re-grant consent |
| WhatsApp returns 400 | No prior template message sent | Send Hello World template from Meta console first |
| Login page loops in prod | Keycloak client not configured | Check `pma` client exists, redirect URI matches |
| Morning briefing not pushed | `NOTIFY_TELEGRAM` is `False` | Set to `True` in `secrets_app.py`, restart worker |
