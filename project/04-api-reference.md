# PMA API Reference — Exhaustive Endpoint Documentation

> **Purpose**: Complete API reference for every HTTP endpoint in the Project-Management-Assistant
> (PMA) backend. This document is authoritative for a from-scratch rebuild.
>
> **Base URL**: `https://pa.mspv.app` (production) or `http://localhost:5000` (dev).
>
> **Authentication**: Unless marked **(public)**, all endpoints require a valid Keycloak-issued
> JWT in the `Authorization: Bearer <token>` header. In development, set `DEV_AUTH_BYPASS=true`
> and pass `X-Dev-User: <username>|<email>|<roles>|<ou>` instead.

---

## Table of Contents

1. [Health Blueprint](#1-health-blueprint)
2. [Auth Blueprint](#2-auth-blueprint)
3. [AI Blueprint](#3-ai-blueprint)
4. [Corpus Blueprint](#4-corpus-blueprint)
5. [MCP Blueprint](#5-mcp-blueprint)
6. [OAuth Discovery Endpoints](#6-oauth-discovery-endpoints)

---

## 1. Health Blueprint

**Blueprint prefix**: `/api/health`
**File**: `code/backend/blueprints/health.py`

---

### GET /api/health

**(public)** — No authentication required.

Liveness probe. Returns a static JSON payload confirming the service is alive and its current
version. Suitable for load balancer health checks.

**Request**: No parameters, no body.

**Response `200 OK`**:

```json
{
    "status": "ok",
    "version": "0.1.41"
}
```

| Field     | Type   | Description                               |
|-----------|--------|-------------------------------------------|
| `status`  | string | Always `"ok"` if the server is running    |
| `version` | string | Current PMA version from package metadata |

**Notes**: This endpoint is in `PUBLIC_PATHS` in `app.py` and bypasses `_resolve_user()`. It is
the only endpoint that should be called by external uptime monitors with no credentials.

---

## 2. Auth Blueprint

**Blueprint prefix**: `/api/auth`
**File**: `code/backend/blueprints/auth.py`

---

### GET /api/auth/config

**(public)** — No authentication required.

Returns Keycloak configuration parameters needed by the Vue/Nuxt frontend to initialise its
OIDC client. Called once at frontend startup before any authenticated requests are made.

**Request**: No parameters, no body.

**Response `200 OK`**:

```json
{
    "realm_url": "https://sso.mspv.app/realms/Office",
    "client_id": "pma"
}
```

| Field       | Type   | Description                                                    |
|-------------|--------|----------------------------------------------------------------|
| `realm_url` | string | Full Keycloak realm URL; frontend uses this to build OIDC URLs |
| `client_id` | string | OIDC client ID registered in Keycloak for this app (`"pma"`)  |

**Notes**:
- `realm_url` is sourced from `KEYCLOAK_REALM_URL` config constant.
- Frontend constructs the JWKS, authorization, and token endpoints by appending standard
  OIDC paths to `realm_url`.
- This endpoint is in `PUBLIC_PATHS` — it is called before login, so it cannot require auth.

---

## 3. AI Blueprint

**Blueprint prefix**: `/api/ai`
**File**: `code/backend/blueprints/ai.py`

The AI blueprint is the core of PMA's conversational interface. All endpoints here require
authentication. The primary endpoint (`/api/ai/chat`) is a streaming SSE endpoint that drives
the chat UI.

---

### POST /api/ai/chat

**Auth required.**

The main Claude chat endpoint. Accepts user messages, builds a rich system prompt from corpus
context, runs multi-turn tool-use with Claude, and streams the response as Server-Sent Events.

**Request headers**:

```
Content-Type: application/json
Authorization: Bearer <token>
```

**Request body**:

```json
{
    "messages": [
        {"role": "user", "content": "What's on my plate today?"},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "Focus on the Acme project."}
    ],
    "ou": "Acme",
    "stream": true
}
```

| Field      | Type    | Required | Description                                                    |
|------------|---------|----------|----------------------------------------------------------------|
| `messages` | array   | Yes      | Conversation turns in Anthropic message format                 |
| `ou`       | string  | No       | Active project/OU context for RAG scoping. If omitted, no scope filter. |
| `stream`   | boolean | No       | Reserved for future use; response is always SSE currently.     |

**Response**: `200 OK`, `Content-Type: text/event-stream`

Each event is on its own line prefixed `data: `, followed by a blank line:

```
data: {"type": "delta", "text": "Here are your tasks for today..."}\n\n
data: {"type": "tool_progress", "event": {"type": "tool_start", "name": "search_corpus", "id": "toolu_01"}}\n\n
data: {"type": "tool_progress", "event": {"type": "tool_end", "name": "search_corpus", "id": "toolu_01", "result": "..."}}\n\n
data: {"type": "done", "result": {...}}\n\n
```

---

#### SSE Event Types

| `type`            | When emitted                                | Payload fields                                     |
|-------------------|---------------------------------------------|----------------------------------------------------|
| `delta`           | Each text chunk from the model              | `text: string`                                     |
| `tool_progress`   | Tool call started or completed              | `event: object` (see below)                        |
| `error`           | Any unhandled exception during streaming    | `message: string`                                  |
| `done`            | After final response, last event            | `result: object` (see below)                       |

**`tool_progress` event shapes**:

```json
{"type": "tool_start", "name": "search_corpus", "id": "toolu_01"}
{"type": "tool_end",   "name": "search_corpus", "id": "toolu_01", "result": "Found 3 relevant chunks..."}
```

**`done` result object**:

```json
{
    "text": "Here are your tasks...",
    "model": "claude-sonnet-4-6",
    "input_tokens": 4823,
    "output_tokens": 312,
    "cache_read_tokens": 3100,
    "cache_write_tokens": 1200,
    "stop_reason": "end_turn",
    "tool_calls": [
        {"name": "search_corpus", "input": {"query": "today tasks"}, "result": "..."}
    ],
    "actions": [
        {"type": "file_edited", "path": "Daily/2026-06-18.md"}
    ]
}
```

| Field               | Type    | Description                                                   |
|---------------------|---------|---------------------------------------------------------------|
| `text`              | string  | Full assistant reply text                                     |
| `model`             | string  | Model ID used for this response                               |
| `input_tokens`      | integer | Total input tokens consumed (all turns in tool loop)          |
| `output_tokens`     | integer | Total output tokens produced                                  |
| `cache_read_tokens` | integer | Tokens served from Anthropic prompt cache                     |
| `cache_write_tokens`| integer | Tokens written to Anthropic prompt cache                      |
| `stop_reason`       | string  | Final stop reason from Anthropic API                          |
| `tool_calls`        | array   | All tool invocations made during this request                 |
| `actions`           | array   | Side-effects performed (file edits, emails sent, etc.)        |

---

#### System Prompt Construction

The system prompt is assembled fresh on **every request** (not cached in memory) in this order:

1. **`SystemPrompt.MD`** — loaded from `code/src/prompts/SystemPrompt.MD` at request time
   (hot-reloaded — file changes take effect without restart).
2. **Current context block**:
   - Current date and time in `Asia/Kolkata` timezone.
   - Active OU/project name from request `ou` field.
3. **`purpose_context`** — from `IndexingService.get_relevant_context()`: the OU/project
   context paragraph (static per-project text).
4. **`gist_summary`** — brief project gist sentence(s) from `IndexingService`.
5. **`toc`** — table of contents / file tree outline from `IndexingService`.
6. **Skills manifest** — formatted list of all 9 skills (name + description only):
   ```
   Available skills:
   - daily-review: Guide the user through their morning/evening daily review workflow
   - monthly-planning: Monthly planning session
   ...
   ```

Anthropic prompt caching (`cache_control: ephemeral`) is applied to the last system block so
that the stable system prefix (blocks 1–6) is cached across requests.

---

#### RAG (Retrieval-Augmented Generation)

For each `/api/ai/chat` request:

1. Extract the last user message text from `messages`.
2. Call `IndexingService.get_relevant_context(query=last_message, project_name=ou, include_archive=...)`.
3. **Archive detection** (`_wants_archive()` internal function):
   - Applies regex patterns matching retrospective language:
     - Words: `retrospective`, `last year`, `previous`, `history`, `archive`, `past`, `2024`,
       `2025` (prior years), etc.
   - If matched: `include_archive=True` → archived corpus files are included in RAG.
   - Otherwise: `include_archive=False` (default).
4. The `relevant_chunks` result is appended to the **user turn** as a context block (not in
   the system prompt), e.g.:

   ```
   [Context from your corpus:]
   --- Daily/2026-06-17.md ---
   <chunk text>
   ---
   ```

---

#### Chat History Persistence

- **Before** building the API call: read the user's full chat history from their per-user
  SQLite database (`data/<user>/db/pma.sqlite3`).
- The history is prepended to the `messages` array from the request.
- **Before** starting streaming: persist the new user message to history.
- **After** streaming completes: persist the assistant reply to history.

This means chat history survives page reloads and is per-user (scoped by JWT identity).

---

#### Available LLM Tools

The following tools are passed to Claude in every `/api/ai/chat` request:

| Tool name        | Description                                                     | Key parameters                            |
|------------------|-----------------------------------------------------------------|-------------------------------------------|
| `load_skill`     | Load full content of a named skill                              | `name: str`                               |
| `grep`           | Grep for a pattern in the md corpus                             | `pattern: str`, `path: str` (optional)    |
| `read_file`      | Read a file from md_root                                        | `path: str`                               |
| `read_src`       | Read a file from `code/src/` (prompts, templates, help)         | `path: str`                               |
| `list_src`       | List a directory within `code/src/`                             | `path: str`                               |
| `list_files`     | List a directory within md_root                                 | `path: str`                               |
| `search_corpus`  | Semantic (ChromaDB) search of the corpus                        | `query: str`, `ou: str`, `include_archive: bool` |
| `send_email`     | Enqueue an outbound email task                                  | `to: str`, `subject: str`, `body: str`    |
| `send_telegram`  | Enqueue a Telegram message task                                 | `message: str`                            |

Tool results > 60,000 characters are truncated (see `llm.py`). Tool calls are looped up to
`MAX_TOOL_ITERATIONS = 8` before the model is forced to produce a final reply.

---

### GET /api/ai/history

**Auth required.**

Returns the current user's full conversation history as stored in their SQLite database.

**Request**: No parameters, no body.

**Response `200 OK`**:

```json
{
    "messages": [
        {"role": "user",      "content": "What's on my plate?",  "timestamp": "2026-06-18T09:14:00+05:30"},
        {"role": "assistant", "content": "Here are your tasks...", "timestamp": "2026-06-18T09:14:03+05:30"}
    ]
}
```

| Field      | Type  | Description                         |
|------------|-------|-------------------------------------|
| `messages` | array | All stored messages, oldest first   |

Each message object follows Anthropic message format with an added `timestamp` field (ISO 8601).

---

### DELETE /api/ai/history

**Auth required.**

Clears the current user's entire conversation history from their SQLite database. This is a
destructive operation with no soft-delete — history is permanently removed.

**Request**: No body.

**Response `200 OK`**:

```json
{"status": "ok"}
```

---

## 4. Corpus Blueprint

**Blueprint prefix**: `/api/corpus`
**File**: `code/backend/blueprints/corpus.py`

The corpus blueprint provides CRUD access to the user's Markdown corpus and manages indexing,
search, and corpus-level operations. All endpoints require authentication.

---

### GET /api/corpus/ous

Returns the list of top-level OU (organizational unit / project) folders within the user's
`md_root`.

**Request**: No parameters.

**Response `200 OK`**:

```json
{
    "ous": ["Acme", "Personal", "TeamAlpha"]
}
```

---

### GET /api/corpus/tree

Returns a recursive file tree for a given OU folder.

**Query parameters**:

| Parameter | Type   | Required | Description            |
|-----------|--------|----------|------------------------|
| `ou`      | string | Yes      | OU folder name to list |

**Response `200 OK`**:

```json
{
    "tree": {
        "Daily": {
            "2026-06-18.md": null,
            "2026-06-17.md": null
        },
        "Plans": {
            "2026-Q2.md": null
        },
        "Index.md": null
    }
}
```

Leaf files have value `null`. Directories have nested dict values. The nesting mirrors the
filesystem structure under `md_root/<ou>/`.

---

### GET /api/corpus/plans

Lists plan files for a given OU.

**Query parameters**:

| Parameter | Type   | Required | Description |
|-----------|--------|----------|-------------|
| `ou`      | string | Yes      | OU name     |

**Response `200 OK`**:

```json
{
    "plans": [
        {"path": "Plans/Acme/2026.md",    "period": "2026"},
        {"path": "Plans/Acme/2026-Q2.md", "period": "2026-Q2"},
        {"path": "Plans/Acme/2026-06.md", "period": "2026-06"}
    ]
}
```

Files are discovered by scanning `Plans/<ou>/` and inferring the period from filenames matching
patterns like `YYYY.md`, `YYYY-QN.md`, `YYYY-MM.md`.

---

### GET /api/corpus/file

Reads a file from the user's `md_root`.

**Query parameters**:

| Parameter | Type   | Required | Description                             |
|-----------|--------|----------|-----------------------------------------|
| `path`    | string | Yes      | Relative path from `md_root`            |

**Response `200 OK`**:

```json
{
    "content": "# 2026-06-18\n\n## Tasks\n- [ ] Review PR\n"
}
```

Returns `404` if the file does not exist. Returns `400` if the path is invalid (contains `..`).

---

### PUT /api/corpus/file

Writes a file (user-authored edit). Creates the file if it does not exist; overwrites if it does.

**Request body**:

```json
{
    "path": "Daily/2026-06-18.md",
    "content": "# 2026-06-18\n\n## Tasks\n- [x] Review PR\n"
}
```

| Field     | Type   | Required | Description                              |
|-----------|--------|----------|------------------------------------------|
| `path`    | string | Yes      | Relative path from `md_root`             |
| `content` | string | Yes      | Full file content (overwrites existing)  |

**Side effects** (triggered in background after response):

1. `indexing_service.refresh_single_file(path)` — re-indexes just this file in ChromaDB.
2. `materialiser.rebuild_project_index()` — refreshes the project-level index document.

**Response `200 OK`**:

```json
{"status": "ok"}
```

---

### GET /api/corpus/sections

Returns all H2-level headings from a file. Used by the frontend to build section-jump navigation.

**Query parameters**:

| Parameter | Type   | Required | Description                  |
|-----------|--------|----------|------------------------------|
| `path`    | string | Yes      | Relative path from `md_root` |

**Response `200 OK`**:

```json
{
    "sections": ["## Tasks", "## Notes", "## Decisions"]
}
```

Headings are returned in document order, with the `##` prefix included.

---

### POST /api/corpus/move-line

Moves or copies a single line from one file to another. The primary use case is "inbox
processing" — moving items from an inbox or scratch file into a specific plan or daily file.

**Request body**:

```json
{
    "source_path": "Inbox.md",
    "line": "- [ ] Follow up with Alice about budget",
    "target_path": "Plans/Acme/2026-Q2.md",
    "action": "move"
}
```

| Field         | Type   | Required | Description                               |
|---------------|--------|----------|-------------------------------------------|
| `source_path` | string | Yes      | Relative path of the source file          |
| `line`        | string | Yes      | The line content to move/copy             |
| `target_path` | string | Yes      | Relative path of the destination file     |
| `action`      | string | Yes      | `"move"` (remove from source) or `"copy"` |

**Line matching**:

- For selections ≥ 12 characters: uses `difflib.SequenceMatcher` — matches if ratio ≥ 0.6.
  This tolerates minor edits in the selection vs. the actual line content.
- For selections < 12 characters: exact match required.
- **Token stripping**: Leading task tokens (`- [ ]`, `- [x]`, `- [>]`, `*`, `-`) are stripped
  before comparison, so the match works even if the user selects the bare text without the bullet.

**Response `200 OK`**:

```json
{"status": "ok"}
```

Returns `400` if no matching line is found in the source file.

---

### DELETE /api/corpus/file

Deletes a file from `md_root` and removes all its index entries from ChromaDB.

**Request body**:

```json
{
    "path": "Daily/2026-01-01.md"
}
```

| Field  | Type   | Required | Description                  |
|--------|--------|----------|------------------------------|
| `path` | string | Yes      | Relative path from `md_root` |

**Response `200 OK`**:

```json
{"status": "ok"}
```

Returns `404` if the file does not exist. Git history is preserved (the deletion is committed
as a `"delete: <filename>"` commit).

---

### POST /api/corpus/line-edit

Atomic line-level edit operation. Always re-reads the file fresh from disk before editing,
avoiding lost-update conflicts.

**Request body**:

```json
{
    "path": "Daily/2026-06-18.md",
    "action": "replace",
    "line": "- [ ] Review PR",
    "new_line": "- [x] Review PR"
}
```

| Field      | Type   | Required | Description                                         |
|------------|--------|----------|-----------------------------------------------------|
| `path`     | string | Yes      | Relative path from `md_root`                        |
| `action`   | string | Yes      | `"append"`, `"replace"`, or `"delete"`              |
| `line`     | string | Yes      | The target line to find (exact match)               |
| `new_line` | string | Conditional | Replacement text (required for `"replace"`)     |

**Actions**:

| Action    | Behaviour                                                          |
|-----------|--------------------------------------------------------------------|
| `append`  | Appends `new_line` after the first occurrence of `line`            |
| `replace` | Replaces the first occurrence of `line` with `new_line`            |
| `delete`  | Removes the first occurrence of `line`                             |

**Response `200 OK`**:

```json
{"status": "ok"}
```

Returns `400` if the target line is not found.

---

### POST /api/corpus/rename

Renames or moves a file within the corpus. Git history is preserved via `git mv`.

**Request body**:

```json
{
    "old_path": "Projects/OldName.md",
    "new_path": "Projects/NewName.md"
}
```

| Field      | Type   | Required | Description                           |
|------------|--------|----------|---------------------------------------|
| `old_path` | string | Yes      | Current relative path from `md_root`  |
| `new_path` | string | Yes      | New relative path from `md_root`      |

**Side effects**:
- ChromaDB entries for the old path are deleted and the file is re-indexed under the new path.

**Response `200 OK`**:

```json
{"status": "ok"}
```

---

### GET /api/corpus/settings

Returns the current user's settings JSON. Settings are stored as a JSON file in the user's
`data_root`.

**Request**: No parameters.

**Response `200 OK`**:

```json
{
    "mcp_enabled": false,
    "news_watch_ous": ["Acme"],
    "default_ou": "Acme"
}
```

If no settings file exists for the user, returns defaults.

---

### PUT /api/corpus/settings

Updates user settings. Merges the supplied fields into the existing settings (partial update).

**Request body** (any subset of settings fields):

```json
{
    "mcp_enabled": true
}
```

**Response `200 OK`**:

```json
{"status": "ok"}
```

---

### GET /api/corpus/queue-stats

Returns diagnostic statistics for the task queue. Used for monitoring and debugging.

**Request**: No parameters.

**Response `200 OK`**:

```json
{
    "pending":    3,
    "retry":      1,
    "processing": 0,
    "done":       142,
    "failed":     2
}
```

Counts are across **all** tasks in the queue (not scoped to the current user).

---

### GET /api/corpus/govern

Returns governance tracking data: recurring tasks owned by team members, project-level tasks,
and daily items — the overview used for team management.

**Query parameters**:

| Parameter      | Type    | Required | Description                             |
|----------------|---------|----------|-----------------------------------------|
| `ou`           | string  | No       | Filter by OU                            |
| `month`        | string  | No       | Month filter (format `YYYY-MM`)         |
| `include_done` | boolean | No       | Include completed tasks (default false) |

**Response `200 OK`**:

```json
{
    "recur_tasks":    [...],
    "project_tasks":  [...],
    "daily_tasks":    [...],
    "people_nicks":   ["Alice", "Bob"]
}
```

---

### POST /api/corpus/news-watch

Enqueues an on-demand news monitoring job for a given project/OU. News-watch jobs use the
Anthropic Batch API (asynchronous) to analyse news relevance and summarise findings.

**Request body**:

```json
{
    "ou": "Acme"
}
```

**Response `200 OK`**:

```json
{
    "job_id": "nw_20260618_acme_a3f7b2"
}
```

The `job_id` is used to poll status.

---

### GET /api/corpus/news-watch/status/{job_id}

Polls the status of a news-watch job. Two-stage status:

1. **Submit stage**: Is the job submitted to Anthropic Batch API?
2. **Anthropic batch state**: Has the batch completed processing?

**Path parameters**:

| Parameter | Type   | Description           |
|-----------|--------|-----------------------|
| `job_id`  | string | ID from `news-watch`  |

**Response `200 OK`** (in-progress):

```json
{
    "status": "processing",
    "results": []
}
```

**Response `200 OK`** (complete):

```json
{
    "status": "complete",
    "results": [
        {
            "headline": "Acme Corp announces Q2 results",
            "summary": "...",
            "relevance": "high",
            "url": "https://..."
        }
    ]
}
```

**Status values**: `"submitted"` → `"processing"` → `"complete"` | `"failed"`

---

### GET /api/corpus/news-feedback

Returns existing news item feedback records for the current user.

**Request**: No parameters.

**Response `200 OK`**:

```json
{
    "feedback": [
        {"item_id": "abc123", "feedback": "up",   "timestamp": "..."},
        {"item_id": "def456", "feedback": "down",  "timestamp": "..."}
    ]
}
```

---

### POST /api/corpus/news-feedback

Records user feedback on a news item (thumbs up or down). Used to train/tune relevance ranking
over time.

**Request body**:

```json
{
    "item_id": "abc123",
    "feedback": "up"
}
```

| Field      | Type   | Required | Description                  |
|------------|--------|----------|------------------------------|
| `item_id`  | string | Yes      | Unique news item identifier  |
| `feedback` | string | Yes      | `"up"` or `"down"`           |

**Response `200 OK`**:

```json
{"status": "ok"}
```

---

### POST /api/corpus/materialise

Triggers an on-demand run of the full materialisation pipeline (`materialise_all()`). Normally
materialisation runs on a schedule, but this endpoint allows manual triggering (e.g. at the
start of a new day).

**Request**: No body required.

**Response `200 OK`**:

```json
{"status": "ok"}
```

This call is synchronous — the response is returned after materialisation completes.

---

### GET /api/corpus/people

Lists people from the `People.md` file in the given OU.

**Query parameters**:

| Parameter | Type   | Required | Description  |
|-----------|--------|----------|--------------|
| `ou`      | string | No       | OU to scope  |

**Response `200 OK`**:

```json
{
    "people": [
        {"name": "Alice Smith", "nick": "Alice", "role": "Engineer", "email": "alice@example.com"},
        {"name": "Bob Jones",   "nick": "Bob",   "role": "PM",        "email": "bob@example.com"}
    ]
}
```

---

### GET /api/corpus/people/nicks

Returns just the nickname list for people. Lightweight endpoint used by autocomplete.

**Query parameters**:

| Parameter | Type   | Required | Description  |
|-----------|--------|----------|--------------|
| `ou`      | string | No       | OU to scope  |

**Response `200 OK`**:

```json
{
    "nicks": ["Alice", "Bob", "Charlie"]
}
```

---

### GET /api/corpus/index-status

Returns diagnostic information about the ChromaDB vector index.

**Request**: No parameters.

**Response `200 OK`**:

```json
{
    "total_chunks":  842,
    "last_sync":     "2026-06-18T09:00:05+05:30",
    "files_indexed": 147
}
```

| Field           | Type    | Description                                          |
|-----------------|---------|------------------------------------------------------|
| `total_chunks`  | integer | Total vector chunks in ChromaDB                      |
| `last_sync`     | string  | ISO 8601 timestamp of last `sync_index()` run        |
| `files_indexed` | integer | Number of distinct `.md` files with vectors in index |

---

### POST /api/corpus/reindex

Triggers a **full** ChromaDB index rebuild (`rebuild_index()`). This is a slow operation
(proportional to corpus size). The slow-request logger skips this path.

**Request**: No body required.

**Response `200 OK`** (returned after rebuild completes):

```json
{
    "status": "ok",
    "chunks_indexed": 842
}
```

---

### GET /api/corpus/git/log

Returns the git commit log for the user's `md_root` corpus repository.

**Query parameters**:

| Parameter | Type    | Required | Description                   |
|-----------|---------|----------|-------------------------------|
| `limit`   | integer | No       | Max commits to return (default 50) |

**Response `200 OK`**:

```json
{
    "commits": [
        {
            "sha":     "a1b2c3d4",
            "message": "AI: updated Acme daily tasks",
            "author":  "Arivu Baalan",
            "date":    "2026-06-18T08:45:12+05:30"
        },
        {
            "sha":     "e5f6a7b8",
            "message": "batch: 2026-06-18T08:00:00",
            "author":  "Arivu Baalan",
            "date":    "2026-06-18T08:00:02+05:30"
        }
    ]
}
```

---

### GET /api/corpus/git/show/{sha}

Returns the unified diff for a specific git commit in the corpus.

**Path parameters**:

| Parameter | Type   | Description          |
|-----------|--------|----------------------|
| `sha`     | string | Git commit SHA       |

**Response `200 OK`**:

```json
{
    "diff": "diff --git a/Daily/2026-06-18.md b/Daily/2026-06-18.md\n..."
}
```

Returns `404` if the SHA is not found in the repository.

---

### GET /api/corpus/search

Literal substring search across the corpus (not semantic — does not use ChromaDB). Used for
exact keyword lookups.

**Query parameters**:

| Parameter | Type    | Required | Description                                            |
|-----------|---------|----------|--------------------------------------------------------|
| `q`       | string  | Yes      | Search query string (literal substring match)          |
| `ou`      | string  | No       | Restrict search to files under this OU folder          |
| `limit`   | integer | No       | Max results to return (default 100)                    |

**Response `200 OK`**:

```json
{
    "results": [
        {
            "path": "Plans/Acme/2026-Q2.md",
            "line": 42,
            "text": "- [ ] Negotiate with Alice about budget allocation"
        },
        {
            "path": "Daily/2026-06-17.md",
            "line": 8,
            "text": "- [x] Alice onboarding done"
        }
    ]
}
```

| Field  | Type    | Description                                  |
|--------|---------|----------------------------------------------|
| `path` | string  | Relative path from `md_root`                 |
| `line` | integer | 1-based line number of the match             |
| `text` | string  | Full text of the matching line               |

---

## 5. MCP Blueprint

**Blueprint prefix**: `/mcp` (no `/api` prefix)
**File**: `code/backend/blueprints/mcp_server.py`
**Protocol**: Model Context Protocol (MCP) 2025-06-18, JSON-RPC 2.0

The MCP blueprint exposes PMA corpus tools to external MCP clients (e.g. Claude.ai connector,
other AI agents). It has its own authentication layer separate from the main JWT auth.

---

### Authentication

MCP endpoints accept two authentication schemes:

1. `X-API-Key: <MCP_API_KEY>` header.
2. `Authorization: Bearer <MCP_API_KEY>` header (same key value).

Additionally, the user's settings must have `mcp_enabled: true`. If either auth check or the
settings check fails, the endpoint returns `401 Unauthorized`.

---

### POST /mcp

The main MCP JSON-RPC 2.0 endpoint. Accepts method calls per the MCP specification.

**Request headers**:

```
Content-Type: application/json
X-API-Key: <MCP_API_KEY>
```

**Request body** (JSON-RPC 2.0):

```json
{
    "jsonrpc": "2.0",
    "id": "req-001",
    "method": "tools/call",
    "params": {
        "name": "read_file",
        "arguments": {
            "path": "Daily/2026-06-18.md"
        }
    }
}
```

**Response** (JSON-RPC 2.0):

```json
{
    "jsonrpc": "2.0",
    "id": "req-001",
    "result": {
        "content": [
            {
                "type": "text",
                "text": "# 2026-06-18\n\n## Tasks\n..."
            }
        ]
    }
}
```

---

#### Available MCP Tools

| Tool name       | Description                                              | Key parameters                                      |
|-----------------|----------------------------------------------------------|-----------------------------------------------------|
| `read_file`     | Read a file from the corpus                              | `path: string`                                      |
| `search_corpus` | Semantic search (ChromaDB)                               | `query: string`, `ou: string`, `include_archive: bool` |
| `grep`          | Literal pattern search in corpus                         | `pattern: string`, `path: string` (optional)        |
| `list_files`    | List a directory in the corpus                           | `path: string`                                      |
| `write_file`    | Write/create a file in the corpus                        | `path: string`, `content: string`                   |
| `apply_edit`    | Apply pma-edit SEARCH/REPLACE block                      | `edit_block: string` (full pma-edit block text)     |
| `read_src`      | Read from `code/src/` (prompts, templates)               | `path: string`                                      |
| `list_src`      | List a directory in `code/src/`                          | `path: string`                                      |

---

### GET /mcp

SSE hello/ping endpoint. Used by MCP clients to verify connectivity and receive server-sent
events (for future push notifications).

**Response**: `200 OK`, `Content-Type: text/event-stream`

Emits a hello ping and then remains open (or closes after one event, depending on client).

---

## 6. OAuth Discovery Endpoints

These endpoints support the MCP OAuth 2.0 flow used by the Claude.ai MCP connector. All are
**public** (no authentication required) — they are discovery documents that OAuth clients fetch
before initiating a flow.

---

### GET /.well-known/oauth-authorization-server

**(public)** OAuth 2.0 Authorization Server Metadata (RFC 8414).

Returned by PMA to tell the Claude.ai connector which endpoints to use for authorization and
token exchange.

**Response `200 OK`** (example):

```json
{
    "issuer": "https://pa.mspv.app",
    "authorization_endpoint": "https://pa.mspv.app/authorize",
    "token_endpoint": "https://pa.mspv.app/token",
    "response_types_supported": ["code"],
    "grant_types_supported": ["authorization_code"],
    "code_challenge_methods_supported": ["S256"]
}
```

---

### GET /.well-known/oauth-protected-resource

**(public)** OAuth 2.0 Protected Resource Metadata.

Tells the MCP client which authorization server protects this resource, enabling automatic
discovery of the full OAuth flow.

**Response `200 OK`** (example):

```json
{
    "resource": "https://pa.mspv.app",
    "authorization_servers": ["https://pa.mspv.app"]
}
```

---

### GET /authorize

**(public)** OAuth 2.0 Authorization Endpoint.

Initiates the authorization code flow. The Claude.ai connector redirects the user here to
obtain consent. PMA proxies this through to Keycloak's own authorization endpoint (using
`MCP_KEYCLOAK_CLIENT_ID`).

**Query parameters** (standard OAuth 2.0 PKCE):

| Parameter               | Description                          |
|-------------------------|--------------------------------------|
| `response_type`         | Must be `"code"`                     |
| `client_id`             | MCP client ID                        |
| `redirect_uri`          | Callback URL                         |
| `scope`                 | Requested scopes                     |
| `state`                 | CSRF state token                     |
| `code_challenge`        | PKCE code challenge                  |
| `code_challenge_method` | Must be `"S256"`                     |

**Response**: `302 Redirect` to Keycloak authorization URL.

---

### POST /token

**(public)** OAuth 2.0 Token Endpoint.

Exchanges an authorization code for tokens. PMA proxies this to Keycloak's token endpoint,
returning the Keycloak-issued access token to the MCP client.

**Request body** (`application/x-www-form-urlencoded`):

| Parameter       | Description                     |
|-----------------|---------------------------------|
| `grant_type`    | `"authorization_code"`          |
| `code`          | Authorization code from `/authorize` |
| `redirect_uri`  | Must match the one used in `/authorize` |
| `code_verifier` | PKCE code verifier              |
| `client_id`     | MCP client ID                   |

**Response `200 OK`** (JSON):

```json
{
    "access_token":  "eyJ...",
    "token_type":    "Bearer",
    "expires_in":    3600,
    "refresh_token": "eyJ..."
}
```

The `access_token` is a standard Keycloak JWT that the MCP client will use in subsequent
requests to the `/mcp` endpoint.

---

## Appendix: Error Responses

All endpoints (except SSE) return errors as JSON:

```json
{
    "error": "Descriptive error message"
}
```

| HTTP Status | Meaning                                                     |
|-------------|-------------------------------------------------------------|
| `400`       | Bad request (missing parameter, invalid path, etc.)         |
| `401`       | Missing or invalid authentication                           |
| `403`       | Authenticated but insufficient permissions                  |
| `404`       | File or resource not found                                  |
| `500`       | Internal server error (logged server-side)                  |

For SSE endpoints (`/api/ai/chat`), errors are streamed as:

```json
{"type": "error", "message": "Error description"}
```

followed by the SSE stream closing.

---

## Appendix: Common Request Patterns

### Authenticated request (curl)

```bash
curl -X GET "https://pa.mspv.app/api/corpus/ous" \
     -H "Authorization: Bearer $ACCESS_TOKEN"
```

### Dev bypass (no Keycloak)

```bash
curl -X GET "http://localhost:5000/api/corpus/ous" \
     -H "X-Dev-User: admin|admin@example.com|admin|Acme"
```

### Streaming chat (curl)

```bash
curl -X POST "http://localhost:5000/api/ai/chat" \
     -H "Authorization: Bearer $ACCESS_TOKEN" \
     -H "Content-Type: application/json" \
     -N \
     -d '{"messages": [{"role": "user", "content": "What are my tasks?"}], "ou": "Acme"}'
```

The `-N` flag disables buffering so SSE events arrive in real time.

---

*End of `04-api-reference.md`*
