# PMA Background Worker & Scheduler

## Overview

The worker is a **separate process** (and separate Docker container) that runs background jobs using APScheduler. It uses the same `pma-backend` Docker image but overrides the CMD:

```yaml
# docker-compose.yml
worker:
  image: REGISTRY/pma-backend:latest
  command: [".venv/bin/python", "-m", "backend.worker"]
```

Entry point: `code/backend/worker.py`

## APScheduler Setup

```python
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
scheduler.start()
```

The scheduler runs in a `BackgroundScheduler` (thread-based). The main thread blocks on signal wait.

## Signal Handling

```python
import signal

def handle_shutdown(signum, frame):
    scheduler.shutdown(wait=False)
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)
```

- SIGTERM/SIGINT: graceful shutdown (scheduler.shutdown, then exit)

## Scheduled Jobs

### 1. `index_sync_job` — ChromaDB Incremental Sync
- **Trigger**: interval, every `INDEX_SYNC_INTERVAL_SEC` seconds (default: 300)
- **Also runs**: once on worker boot (initial sync)
- **Action**: `IndexingService(user_id).sync_index()`
- **What it does**:
  - Lists all .md files in `DATA_ROOT/<user>/md/`
  - Compares disk mtime vs ChromaDB metadata `mtime` field
  - Upserts changed/new files into ChromaDB
  - Removes entries for deleted files
- **Cost**: lightweight for small changes; re-indexes per-file only when mtime differs

### 2. `queue_drain_job` — Task Queue Processor
- **Trigger**: interval, every 15 seconds
- **Action**: `task_queue.drain(batch_size=20)`
- **What it does**:
  - Fetches up to 20 pending/retry tasks where `next_retry_at <= now`
  - Dispatches each task to its registered handler
  - On success: marks task as `done`, stores result
  - On failure: marks as `retry`, increments attempts, sets next_retry_at (exponential backoff)
  - After max_attempts failures: marks as `failed`
- **Handlers registered**:
  - `email` — sends email via Office 365 MSAL
  - `telegram` — sends Telegram message via Bot API
  - `jira_create` — creates Jira issue via REST API
  - `news_watch` — submits news batch to Anthropic

### 3. `materialise_job` — Nightly Materialisation
- **Trigger**: cron, `hour=0, minute=0` (midnight)
- **Action**: `materialiser.materialise_all(user)`
- **What it does** (three stages, deterministic/no LLM):
  1. **Non-daily**: Recur files → Plans files (monthly/quarterly/yearly)
     - Reads schedule specs from `Recur/` directory
     - Computes due dates
     - Inserts tasks into appropriate Plans files with idempotency markers
  2. **Daily**: Seeds `Daily/<today>.md`
     - Carry-forward: copies unchecked `- [ ]` tasks from last daily file
     - Plan pipe: `apply_plan_pipe()` moves `start:<today>` / `due:<today>` tasks from Plans → marks `[>]`
     - Daily checklist: copies from `Recur/Daily.md`
  3. **Govern**: Team-owned recur → `Govern/<YYYY-MM>.md`
     - Also rebuilds project index
- **Idempotency**: safe to run multiple times; stable markers prevent duplicate insertion

### 4. `commit_pending_job` — Batch Git Commit
- **Trigger**: cron, every hour (e.g. `minute=0`)
- **Action**: `md_patcher.commit_pending(user)`
- **What it does**:
  - `git add -A` in md_root
  - If there are staged changes: commits as `batch: <ISO timestamp>`
  - Git author: `Arivu Baalan <arivu@smtw.in>`
- **Purpose**: user file saves via PUT /api/corpus/file are written to disk immediately but NOT committed. The hourly job commits them in batch.

### 5. `housekeeping_job` — Nightly Cleanup
- **Trigger**: cron, `hour=23, minute=0`
- **Action**: miscellaneous cleanup tasks
- **What it does** (likely):
  - Prune old task_queue entries (done/failed, older than N days)
  - Clean up stale ChromaDB entries
  - Log stats

### 6. `news_watch_submit_job` — News Batch Submission
- **Trigger**: cron, `hour=0, minute=0` (midnight, same as materialise)
- **Disabled if**: `NEWS_WATCH_CRON_DISABLED=True` in config
- **Action**: for each project with news_watch enabled, submits batch to Anthropic Message Batches API
- **What it does**:
  - Reads project files to find active projects with news keywords
  - Creates Anthropic Message Batch requests (one per project)
  - Enqueues batch IDs in task_queue for polling
- **Why batch**: Anthropic Message Batches API is async (submit → wait → poll results). Much cheaper than real-time requests.

### 7. `news_watch_poll_job` — News Batch Result Polling
- **Trigger**: interval, every 300 seconds
- **Action**: polls pending Anthropic batch IDs for results
- **What it does**:
  - Checks status of submitted batches
  - If complete: processes results, writes news summaries to project files
  - Handles batch failures gracefully

### 8. `jira_sync_job` — Jira Integration Sync
- **Trigger**: interval, every 900 seconds (15 minutes)
- **Condition**: only runs if Jira is configured (`JIRA_URL` set)
- **Action**: syncs Jira issues with corpus
- **What it does**:
  - Reads tasks in corpus with `JIRA:<KEY>` tokens
  - Fetches issue status from Jira Cloud REST API
  - Updates task status in MD files if changed (e.g. Jira issue closed → mark `[x]`)

## Task Queue Deep Dive

### Database Schema
```sql
-- DATA_ROOT/queue.sqlite3
CREATE TABLE task_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    payload TEXT NOT NULL,           -- JSON string
    status TEXT DEFAULT 'pending',   -- pending | retry | processing | done | failed
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    next_retry_at REAL,              -- Unix timestamp (NULL = ready immediately)
    created_at REAL NOT NULL,        -- Unix timestamp
    updated_at REAL NOT NULL,        -- Unix timestamp
    result TEXT,                     -- JSON result string on success
    error TEXT                       -- Error message string on failure
);
CREATE INDEX idx_task_queue_status ON task_queue(status, next_retry_at);
```

### Exponential Backoff
```
BACKOFF_BASE_SEC = 30

Attempt 1 fails → next_retry_at = now + 30s
Attempt 2 fails → next_retry_at = now + 60s
Attempt 3 fails → next_retry_at = now + 120s → status = 'failed'
```

Formula: `delay = BACKOFF_BASE_SEC * (2 ** (attempt - 1))`

### Task Handlers

#### `email` handler
- Payload: `{"to": "...", "subject": "...", "body": "..."}`
- Uses MSAL (`msal` package) with O365 credentials
- Sends via Microsoft Graph API

#### `telegram` handler
- Payload: `{"message": "..."}`
- Uses Bot API: `POST https://api.telegram.org/bot{TOKEN}/sendMessage`
- Target: `TELEGRAM_CHAT_ID`

#### `jira_create` handler
- Payload: `{"project_key": "...", "summary": "...", "description": "...", "issue_type": "Task"}`
- Uses Jira Cloud REST API v3
- Auth: Basic auth with `JIRA_EMAIL:JIRA_TOKEN`

#### `news_watch` handler
- Payload: `{"ou": "...", "keywords": [...], "batch_id": "..."}`
- Manages Anthropic Message Batches lifecycle

## Worker Boot Sequence

1. Import all handlers, register with task_queue
2. Create APScheduler instance
3. Add all jobs with their triggers
4. `scheduler.start()`
5. Run `index_sync_job()` immediately (initial sync on boot)
6. Block main thread: `signal.pause()` (or loop with sleep)

## Multi-User Considerations

The worker currently processes jobs for a single user (or iterates over all users). Key points:
- `queue.sqlite3` is shared across users (keyed by `user_id` in payload)
- IndexingService is instantiated per user_id
- MaterialisationService is per-user
- Worker must have access to `DATA_ROOT` (all users' data)

## Observability

- All jobs log to stdout (APScheduler + Python logging)
- `docker logs pma-worker` shows:
  - Job execution start/end
  - Sync stats (files updated, removed)
  - Queue drain stats (processed, failed)
  - Any exceptions (logged + job continues)
- Health monitoring: check `GET /api/corpus/index-status` (backend) which shows last_sync time
- Queue health: `GET /api/corpus/queue-stats` shows pending/retry/failed counts

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| Job raises exception | APScheduler logs error, job runs again next interval |
| ChromaDB unavailable | `index_sync_job` fails, logs error, retries next cycle |
| Anthropic API timeout | Chat tool fails gracefully; news_watch task retried |
| Jira unreachable | `jira_sync_job` skipped, retried next interval |
| Git lock conflict | `commit_pending_job` fails, retried next hour |
| Task max_attempts reached | Status → `failed`, manual investigation needed |
