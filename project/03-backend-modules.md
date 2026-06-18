# PMA Backend Modules — Exhaustive Reference

> **Purpose**: Complete technical documentation for every backend Python module in the
> Project-Management-Assistant (PMA). This document is authoritative for a from-scratch rebuild.
> All paths are relative to the repository root unless stated otherwise.

---

## Table of Contents

1. [app.py — Flask Application Factory](#1-apppy--flask-application-factory)
2. [config.py — Configuration & Data Classes](#2-configpy--configuration--data-classes)
3. [auth_utils.py — Keycloak JWT Validation](#3-auth_utilspy--keycloak-jwt-validation)
4. [llm.py — Claude API Wrapper](#4-llmpy--claude-api-wrapper)
5. [md_patcher.py — Markdown File Editor](#5-md_patcherpy--markdown-file-editor)
6. [indexing_service.py — ChromaDB Vector Store](#6-indexing_servicepy--chromadb-vector-store)
7. [materialiser.py — Deterministic Recurring Task Pipeline](#7-materialiserpy--deterministic-recurring-task-pipeline)
8. [task_queue.py — SQLite Task Queue](#8-task_queuepy--sqlite-task-queue)
9. [skills.py — Progressive Disclosure Skills](#9-skillspy--progressive-disclosure-skills)

---

## 1. `app.py` — Flask Application Factory

**File path**: `code/backend/app.py`

### Overview

The single entry-point for the Flask application. Produces a configured `Flask` instance via the
factory pattern (enabling test isolation). All blueprints, middleware hooks, and cross-cutting
concerns (auth, timing) are wired here.

---

### `create_app() -> Flask`

```python
def create_app() -> Flask:
    ...
```

**Responsibilities**:

1. Instantiates `Flask(__name__)`.
2. Registers all route blueprints (see below).
3. Registers a `before_request` hook: `_resolve_user()`.
4. Registers an `after_request` hook: slow-request logger.

#### Blueprints registered

| Blueprint import name | URL prefix        | Purpose                                 |
|-----------------------|-------------------|-----------------------------------------|
| `health`              | `/api/health`     | Liveness probe                          |
| `auth`                | `/api/auth`       | Keycloak config handshake               |
| `ai`                  | `/api/ai`         | Claude chat + history                   |
| `corpus`              | `/api/corpus`     | Markdown corpus CRUD + indexing         |
| `jira`                | `/api/jira`       | Jira integration                        |
| `mcp`                 | (root)            | Model Context Protocol JSON-RPC server  |

---

### `PUBLIC_PATHS`

```python
PUBLIC_PATHS = {
    "/api/health",
    "/api/auth/config",
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource",
    "/authorize",
    "/token",
}
```

These paths are **exempt from JWT authentication**. Any request whose `request.path` is in this
set passes the `before_request` hook without credential validation.

---

### MCP Path Handling

Paths beginning with `/mcp` bypass the standard Bearer-JWT middleware. They have **their own
authentication layer** via `X-API-Key` header (see MCP blueprint). The `_resolve_user()` function
detects MCP paths and skips standard auth for them.

---

### `_resolve_user()`

```python
def _resolve_user() -> None | Response:
    ...
```

`before_request` hook. Called on every inbound request. Logic flow:

```
request arrives
    │
    ├─ path in PUBLIC_PATHS? → return None (allow through)
    │
    ├─ path starts with /mcp? → return None (MCP handles own auth)
    │
    ├─ DEV_AUTH_BYPASS is True?
    │       └─ read X-Dev-User header (format: "username|email|roles|ou")
    │          → build CurrentUser from header fields
    │          → set g.user = CurrentUser(...)
    │          → return None
    │
    └─ else (production auth)
            └─ read Authorization header
               → expect "Bearer <token>"
               → call decode_token(token) from auth_utils
               → call roles_from_claims(claims)
               → set g.user = CurrentUser(
                       username = claims["preferred_username"],
                       email    = claims["email"],
                       roles    = roles,
                       ou       = claims.get("ou", ""),
                  )
               → on any failure → return 401 JSON error
```

`g.user` is available to all subsequent request handlers as a `CurrentUser` dataclass instance.

---

### After-Request: Slow-Request Logger

```python
@app.after_request
def _log_slow(response: Response) -> Response:
    ...
```

- Computes elapsed time from a timestamp stored at request start (set in `before_request`).
- If elapsed > **1000 ms**: logs `WARNING` with method, path, and elapsed time.
- **Skips logging** for these paths (they are inherently long-running or streaming):
  - `/api/ai/chat`
  - `/api/corpus/reindex`
  - `/mcp/call`
- Always returns the response unchanged.

---

## 2. `config.py` — Configuration & Data Classes

**File path**: `code/backend/config.py`

### Overview

Centralises all runtime configuration: path constants, secret values, and the `CurrentUser`
dataclass. Acts as a single source of truth — no other module should read environment variables
directly.

---

### `_get(key: str, default=None) -> Any`

```python
def _get(key: str, default=None) -> Any:
    ...
```

**Resolution order** (first non-None wins):

1. `os.environ.get(key)` — environment variable.
2. Import `secrets_app` module (optional file at `code/backend/secrets_app.py`) and read
   `getattr(secrets_app, key, None)`.
3. Return `default`.

**Design rule**: Never raises on a missing secret. Callers receive `None` or their supplied
default. This allows the app to start in partial-config states (e.g. no Jira credentials) and
fail gracefully per-feature.

---

### `CurrentUser` Dataclass

```python
@dataclass(frozen=True)
class CurrentUser:
    username: str
    email: str
    roles: list[str]
    ou: str          # organizational unit / active project context
```

`frozen=True` — instances are immutable after construction.

#### Computed Properties

| Property      | Returns                                    | Notes                                     |
|---------------|--------------------------------------------|-------------------------------------------|
| `data_root`   | `Path(DATA_ROOT) / username`               | Per-user data directory root              |
| `md_root`     | `data_root / "md"`                         | Git-tracked Markdown corpus root          |
| `db_path`     | `data_root / "db" / "pma.sqlite3"`         | SQLite database (chat history, queue)     |
| `v_db_path`   | `data_root / "db" / "chroma"`              | ChromaDB vector store directory           |

All properties return `pathlib.Path` objects.

---

### Path Constants

These are module-level constants resolved at import time.

| Constant              | Value / Resolution                              | Purpose                              |
|-----------------------|-------------------------------------------------|--------------------------------------|
| `CODE_ROOT`           | `Path(__file__).parent.parent`                  | `code/` directory                    |
| `REPO_ROOT`           | `CODE_ROOT.parent`                              | Repository root                      |
| `SRC_ROOT`            | `CODE_ROOT / "src"`                             | `code/src/`                          |
| `PROMPTS_DIR`         | `SRC_ROOT / "prompts"`                          | Prompt markdown files                |
| `SKILLS_DIR`          | `PROMPTS_DIR / "skills"`                        | Skill markdown files                 |
| `HELP_ROOT`           | `SRC_ROOT / "help"`                             | Help content                         |
| `DATA_ROOT`           | `_get("DATA_ROOT")`                             | Base directory for all user data     |
| `TEMPLATES_SEED_ROOT` | `SRC_ROOT / "templates"`                        | Shipped template files               |
| `TEMPLATES_ROOT`      | Per-user: `CurrentUser.md_root / "templates"`   | User's customised templates          |

---

### Secret / Config Constants

All resolved via `_get()`.

| Constant                    | Type   | Description                                                     |
|-----------------------------|--------|-----------------------------------------------------------------|
| `EMBED_MODEL`               | `str`  | `"BAAI/bge-small-en-v1.5"` — embedding model for ChromaDB      |
| `DEV_AUTH_BYPASS`           | `bool` | If `True`, reads user from `X-Dev-User` header (dev only)       |
| `KEYCLOAK_REALM_URL`        | `str`  | e.g. `https://sso.mspv.app/realms/Office`                       |
| `ANTHROPIC_API_KEY`         | `str`  | Anthropic API key for Claude                                    |
| `O365_CLIENT_ID`            | `str`  | Microsoft 365 OAuth client ID (email integration)               |
| `O365_CLIENT_SECRET`        | `str`  | Microsoft 365 OAuth client secret                               |
| `O365_TENANT_ID`            | `str`  | Microsoft 365 tenant ID                                         |
| `TELEGRAM_BOT_TOKEN`        | `str`  | Telegram bot token                                              |
| `TELEGRAM_CHAT_ID`          | `str`  | Telegram target chat ID                                         |
| `JIRA_URL`                  | `str`  | Jira instance base URL                                          |
| `JIRA_EMAIL`                | `str`  | Jira account email                                              |
| `JIRA_TOKEN`                | `str`  | Jira API token                                                  |
| `JIRA_PROJECT_KEY`          | `str`  | Default Jira project key                                        |
| `MCP_API_KEY`               | `str`  | Shared secret for MCP endpoint authentication                   |
| `MCP_KEYCLOAK_CLIENT_ID`    | `str`  | Keycloak client ID used for MCP OAuth discovery                 |
| `INDEX_SYNC_INTERVAL_SEC`   | `int`  | `300` — seconds between incremental index sync runs             |
| `NEWS_WATCH_CRON_DISABLED`  | `bool` | If `True`, suppresses scheduled news-watch job                  |

---

## 3. `auth_utils.py` — Keycloak JWT Validation

**File path**: `code/backend/auth_utils.py`

### Overview

Provides JWT decode and role extraction for Keycloak-issued RS256 tokens. Handles the
container-networking problem of Keycloak being reachable only via internal IP from the backend
container.

---

### `_make_internal_url(url: str) -> str`

```python
def _make_internal_url(url: str) -> str:
    ...
```

**Problem**: The public Keycloak URL (`https://sso.mspv.app/realms/Office`) is routed through
Caddy reverse proxy. Backend containers cannot reach Caddy — they need to hit Keycloak directly
on the Docker network.

**Solution**: Rewrites the URL:

```
Input:  https://sso.mspv.app/realms/Office
Output: http://<KEYCLOAK_HOST_IP>:8080/realms/Office
```

`KEYCLOAK_HOST_IP` is read from config/env. Used internally in `decode_token` so that JWKS
fetches go directly to Keycloak's HTTP port, bypassing the reverse proxy.

---

### `decode_token(token: str) -> dict`

```python
def decode_token(token: str) -> dict:
    ...
```

**Algorithm**: RS256 (asymmetric — Keycloak signs with its private key, backend verifies with
the public key fetched from JWKS endpoint).

**Steps**:

1. Construct JWKS URL: `_make_internal_url(KEYCLOAK_REALM_URL) + "/protocol/openid-connect/certs"`.
2. `GET` the JWKS JSON.
3. Use `PyJWT` (or `python-jose`) to select the matching key by `kid` in token header.
4. Decode and verify the token:
   - Algorithm: `RS256`
   - `verify_aud=False` — PMA does not enforce audience claim (Keycloak may omit it or set it
     to the realm; we trust the signing key alone).
   - Checks `azp` (authorized party) claim to ensure the token was issued to a known client.
5. **Retry on JWKS error**: If the JWKS fetch fails (e.g. key rotation just happened), retries
   once before propagating the error. This avoids transient 401s during key rollover.
6. Returns the full decoded claims `dict` on success.
7. Raises on invalid signature, expired token, or decode failure.

---

### `roles_from_claims(claims: dict) -> list[str]`

```python
def roles_from_claims(claims: dict) -> list[str]:
    ...
```

**Keycloak role locations in JWT**:

| Source                                        | Scope         |
|-----------------------------------------------|---------------|
| `claims["realm_access"]["roles"]`             | Realm-level   |
| `claims["resource_access"][<client_id>]["roles"]` | Client-level |

`<client_id>` is `MCP_KEYCLOAK_CLIENT_ID` (or the primary app client id as configured).

Returns a combined `list[str]` of all role names from both sources. Deduplication is not
guaranteed — callers should use `in` membership tests.

---

## 4. `llm.py` — Claude API Wrapper

**File path**: `code/backend/llm.py`

### Overview

Thin, synchronous wrapper around the Anthropic Python SDK. Handles multi-turn tool-use loops,
streaming with interleaved tool events, prompt caching, and result normalisation.

---

### Constants

```python
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOOL_ITERATIONS = 8
```

- `DEFAULT_MODEL` — used when callers do not specify a model.
- `MAX_TOOL_ITERATIONS` — hard cap on tool-use rounds per `chat()` / `chat_stream()` call.
  Prevents runaway loops if the model keeps calling tools.

---

### `Tool` Dataclass

```python
@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict    # JSON Schema object (type, properties, required, ...)
    handler: Callable[[dict], str]   # receives tool input dict, returns string result
```

- `name` — unique identifier, used in API tool definitions and in result routing.
- `description` — shown to the model; should be precise about when to use the tool.
- `input_schema` — standard JSON Schema, passed verbatim to the Anthropic API as the tool's
  parameter schema.
- `handler` — synchronous callable. Receives the model's chosen input parameters as a `dict`.
  **Must return a `str`**. The string becomes the tool result in the conversation.

---

### `ChatResult` Dataclass

```python
@dataclass
class ChatResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    stop_reason: str
    tool_calls: list[dict]
```

| Field                | Description                                                          |
|----------------------|----------------------------------------------------------------------|
| `text`               | Final assistant text (after all tool rounds)                         |
| `model`              | Model ID string as returned by the API                               |
| `input_tokens`       | Total input tokens consumed across all turns in the loop             |
| `output_tokens`      | Total output tokens produced                                         |
| `cache_read_tokens`  | Tokens served from Anthropic prompt cache (cost ≈ 10% of normal)    |
| `cache_write_tokens` | Tokens written into Anthropic prompt cache                           |
| `stop_reason`        | Final stop reason: `"end_turn"`, `"max_tokens"`, etc.               |
| `tool_calls`         | List of `{"name": ..., "input": ..., "result": ...}` dicts for each tool call made |

---

### `chat(messages, system, tools, model) -> ChatResult`

```python
def chat(
    messages: list,
    system: list[dict],
    tools: list[Tool],
    model: str = DEFAULT_MODEL,
) -> ChatResult:
    ...
```

**Synchronous** multi-turn chat with tool-use support.

**Loop**:

```
iteration = 0
while iteration < MAX_TOOL_ITERATIONS:
    response = anthropic_client.messages.create(
        model=model,
        system=_apply_cache_control(system),
        messages=messages,
        tools=[tool_schema for each Tool],
        ...
    )

    if response.stop_reason == "end_turn":
        break  # done

    if response.stop_reason == "tool_use":
        tool_results = []
        for tool_use_block in response.content:
            handler = find handler by tool_use_block.name
            result_str = handler(tool_use_block.input)
            if len(result_str) > 60_000:
                result_str = result_str[:60_000] + "\n[TRUNCATED]"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_block.id,
                "content": result_str,
            })
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})
        iteration += 1
        continue

    break  # other stop reason

accumulate token counts across iterations
return ChatResult(...)
```

---

### `chat_stream(messages, system, tools, model) -> Iterator`

```python
def chat_stream(
    messages: list,
    system: list[dict],
    tools: list[Tool],
    model: str = DEFAULT_MODEL,
) -> Iterator:
    ...
```

**Generator** — yields events as they arrive from the Anthropic streaming API, with tool-use
interleaved.

**Yielded types**:

| Yielded value                                                  | Meaning                                  |
|----------------------------------------------------------------|------------------------------------------|
| `str` (text chunk)                                             | Delta text from model                    |
| `{"type": "tool_start", "name": "...", "id": "..."}`           | Model began a tool call                  |
| `{"type": "tool_end", "name": "...", "id": "...", "result": "..."}` | Tool handler finished, result available |
| `ChatResult` (final yield)                                     | Full result with usage statistics        |

**Behaviour**:

- Uses `anthropic_client.messages.stream()` context manager.
- For text deltas: yields each chunk as a plain `str`.
- For tool-use blocks: yields `tool_start` event → executes handler → yields `tool_end` event.
- Continues the multi-turn loop (up to `MAX_TOOL_ITERATIONS`) within the generator.
- Final `ChatResult` is yielded as the **last item** (callers must check `isinstance(item, ChatResult)` to detect it).

---

### `_apply_cache_control(system_blocks: list[dict]) -> list[dict]`

```python
def _apply_cache_control(system_blocks: list[dict]) -> list[dict]:
    ...
```

Tags the **last block** in the system prompt list with:

```python
{"cache_control": {"type": "ephemeral"}}
```

This instructs Anthropic's caching layer to cache the system prompt prefix up to that block.
Cache TTL is approximately 5 minutes. Subsequent requests that share the same system prefix
pay only ~10% of the normal input token cost for those cached tokens.

**Why last block**: All preceding blocks are implicitly included in the cache prefix. The last
block is the boundary marker — anything after it (user messages) is not cached.

Returns a new list (does not mutate the input).

---

### Tool Result Truncation

```python
MAX_TOOL_RESULT_CHARS = 60_000
```

If a tool handler returns a string longer than 60,000 characters, `chat()` and `chat_stream()`
both truncate it to 60,000 chars and append:

```
\n[TRUNCATED — result was N chars, showing first 60000]
```

This prevents context-window exhaustion from large file reads or grep outputs.

---

## 5. `md_patcher.py` — Markdown File Editor

**File path**: `code/backend/md_patcher.py`

### Overview

Implements the `pma-edit` block format — a SEARCH/REPLACE protocol that allows the LLM to make
precise edits to Markdown files in the corpus. Also provides primitive file-write and batch-commit
utilities used by other parts of the backend.

---

### The `pma-edit` Block Format

The LLM embeds edit instructions in its reply as fenced code blocks:

````
```pma-edit
file: <path-relative-to-md_root>
<<<<<<< SEARCH
<exact existing content to find in the file>
=======
<new content to replace the found content with>
>>>>>>> REPLACE
```
````

**Rules**:

- `file:` header is the path relative to `md_root` (e.g. `Daily/2026-06-18.md`).
- SEARCH text must match **exactly one** location in the file (case-sensitive, byte-exact after
  CRLF normalisation).
- REPLACE text can be empty (deletion) or any new content.
- Multiple `pma-edit` blocks can appear in a single reply; all are applied atomically.

---

### `Actor` Named Tuple / Dataclass

```python
ASSISTANT_AUTHOR = Actor("Arivu Baalan", "arivu@smtw.in")
MCP_AUTHOR       = Actor("Arivu Baalan", "mcp@smtw.in")
```

Used as the git `--author` when committing AI-originated changes. Differentiates human edits
(committed with the user's own git identity) from AI edits.

---

### `apply_reply_if_edit(reply: str, user: CurrentUser, commit_summary: str = None) -> list[str]`

```python
def apply_reply_if_edit(
    reply: str,
    user: CurrentUser,
    commit_summary: str = None,
) -> list[str]:
    ...
```

**Returns**: list of file paths that were modified.

**Algorithm**:

```
1. Extract all ```pma-edit ... ``` blocks from `reply` using regex.
   If none found → return [] immediately.

2. For each block:
   a. Parse the `file:` header line → get relative path.
   b. Path validation:
      - Must not contain ".." components.
      - `md_root / path` must resolve within md_root (no path traversal).
      → If invalid → raise ValueError, abort.

   c. Read current file content from disk.
   d. Normalise CRLF → LF in both the file content and the SEARCH text.
   e. Count occurrences of SEARCH text in file:
      - 0 occurrences → error: "SEARCH text not found"
      - 2+ occurrences → error: "ambiguous match"
      - Exactly 1 → proceed.
   f. Replace SEARCH text with REPLACE text → new content string.
   g. Store (path, original_content, new_content) for rollback.

3. If ALL blocks parsed successfully:
   a. Write each new_content to disk (md_root / path).
   b. Git commit all modified files:
      - Message: "AI: <commit_summary>" or "AI: edit" if no summary.
      - Author: ASSISTANT_AUTHOR (Arivu Baalan <arivu@smtw.in>).
   c. Return list of modified paths.

4. If ANY block fails (at step 2):
   - Do NOT write any file.
   - Raise the error.
   (Rollback is trivial because nothing was written yet — original content held in memory.)
```

**CRLF Normalisation**: Applied to both the SEARCH needle and the file haystack before matching,
ensuring Windows line endings in either source do not cause false mismatches.

---

### `write_file(path: Path, content: str, user: CurrentUser, commit_prefix: str = None) -> None`

```python
def write_file(
    path: Path,
    content: str,
    user: CurrentUser,
    commit_prefix: str = None,
) -> None:
    ...
```

General-purpose file writer used by the corpus blueprint, materialiser, and other callers.

**Behaviour**:

| `commit_prefix` | Behaviour                                                                   |
|-----------------|-----------------------------------------------------------------------------|
| `None`          | Write to disk. Leave file **unstaged**. Will be picked up by `commit_pending_job` (hourly). |
| `str`           | Write to disk. Immediately `git add` + `git commit` as `"<commit_prefix>: <filename>"`.    |

---

### `commit_pending(user: CurrentUser) -> None`

```python
def commit_pending(user: CurrentUser) -> None:
    ...
```

Used by the hourly background job (`commit_pending_job`) to batch-commit accumulated unstaged
changes.

**Steps**:

1. `git -C md_root add -A` — stage all changes.
2. Check if there is anything to commit (`git status --porcelain`).
3. If yes: `git commit -m "batch: <ISO timestamp>" --author "Arivu Baalan <arivu@smtw.in>"`.
4. If nothing staged: no-op.

---

## 6. `indexing_service.py` — ChromaDB Vector Store

**File path**: `code/backend/indexing_service.py`

### Overview

Manages semantic search over the user's Markdown corpus via ChromaDB (local, persistent vector
store) and FastEmbed embeddings. Provides both full-rebuild and incremental-sync modes.

---

### `IndexingService`

```python
class IndexingService:
    def __init__(self, user_id: str):
        ...
```

**Constructor**:
- `user_id` is `CurrentUser.username`.
- Locates ChromaDB at `CurrentUser.v_db_path` = `data/<user>/db/chroma/`.
- Opens (or creates) a ChromaDB collection named `"md_corpus"`.
- Uses a **singleton** `FastEmbedEmbedding("BAAI/bge-small-en-v1.5")` — shared across all
  `IndexingService` instances in the process to avoid loading the model multiple times.

---

### `rebuild_index(self) -> None`

```python
def rebuild_index(self) -> None:
    ...
```

**Full rebuild** — destroys and recreates the entire index.

**Steps**:

1. Delete the `"md_corpus"` ChromaDB collection entirely (drops all vectors + metadata).
2. Re-create a fresh collection.
3. Walk `md_root` recursively, collecting all `.md` files.
4. For each file:
   - Use LlamaIndex `SimpleDirectoryReader` to load the file.
   - Parse into nodes with `MarkdownNodeParser` (splits on headings, produces coherent chunks).
   - Store each node in ChromaDB with metadata:

     | Metadata key | Value                              |
     |--------------|------------------------------------|
     | `ou`         | OU folder name (top-level dir)     |
     | `path`       | Relative path from md_root         |
     | `mtime`      | File modification timestamp (float)|
     | `archived`   | `"true"` or `"false"` string       |

**Note**: `archived` is determined by whether the file is under an `Archive/` directory in the
corpus.

---

### `refresh_single_file(self, path: Path) -> None`

```python
def refresh_single_file(self, path: Path) -> None:
    ...
```

**Incremental update** for a single file. Used immediately after a user saves a file via the
corpus API.

**Steps**:

1. Delete all ChromaDB entries where metadata `path` == relative path of this file.
2. Re-index the file (same parser pipeline as `rebuild_index`).

This is faster than a full rebuild and keeps the index consistent without user-visible delay.

---

### `sync_index(self) -> None`

```python
def sync_index(self) -> None:
    ...
```

**Efficient incremental sync** — the background job called every `INDEX_SYNC_INTERVAL_SEC`
(300 s) seconds.

**Algorithm**:

```
disk_files = {relative_path: mtime} for all .md files in md_root

indexed_files = {path: mtime} from ChromaDB metadata

for path in disk_files:
    if path not in indexed_files:
        # New file → index it
    elif disk_files[path] > indexed_files[path]:
        # Modified → refresh_single_file

for path in indexed_files:
    if path not in disk_files:
        # Deleted → remove from ChromaDB
```

Net result: only changed/new/deleted files are processed. Scales to thousands of files with
minimal I/O.

---

### `get_relevant_context(self, query, project_name=None, top_k=5, include_archive=False) -> dict`

```python
def get_relevant_context(
    self,
    query: str,
    project_name: str = None,
    top_k: int = 5,
    include_archive: bool = False,
) -> dict:
    ...
```

**Returns**:

```python
{
    "relevant_chunks": str,    # top-k retrieved text chunks, concatenated
    "toc": str,                # table of contents (file tree / heading outline)
    "gist_summary": str,       # short project gist sentence(s)
    "purpose_context": str,    # OU/project context paragraph
}
```

**RAG query**:

- Embeds `query` with the FastEmbed model.
- Queries ChromaDB with metadata filters:
  - If `project_name` is set: `ou` EQ `project_name` (scope to one project).
  - If `include_archive=False`: `archived` EQ `"false"` (exclude archived files).
- Returns top `top_k` chunks by cosine similarity.
- Concatenates chunk texts into `relevant_chunks`.

**`toc`**, **`gist_summary`**, **`purpose_context`** are derived from deterministic reads
of special files in the corpus (e.g. `<OU>/Index.md`, `<OU>/Gist.md`, `<OU>/Purpose.md`)
rather than from vector search.

---

### `get_file_content(self, path: str) -> str`

```python
def get_file_content(self, path: str) -> str:
    ...
```

Direct file read — no vector lookup. Returns raw text of `md_root / path`. Used by the `read_file`
LLM tool.

---

## 7. `materialiser.py` — Deterministic Recurring Task Pipeline

**File path**: `code/backend/materialiser.py`

### Overview

A pure-Python pipeline that "materialises" the corpus — seeding Daily notes, propagating
recurring tasks into plan files, and generating governance tracking documents. All operations
are idempotent: running `materialise_all` twice produces the same result as running it once.

---

### `materialise_all(user: CurrentUser) -> None`

```python
def materialise_all(user: CurrentUser) -> None:
    ...
```

Top-level entry point. Runs three stages **in order** (order matters — stage 2 depends on stage 1
outputs):

| Stage | Function                 | What it produces                            |
|-------|--------------------------|---------------------------------------------|
| 1     | `materialise_non_daily`  | Recur → Plan files (monthly/quarterly/yearly)|
| 2     | `materialise_daily`      | `Daily/<date>.md`                           |
| 3     | `materialise_govern`     | Govern tracking + project index              |

---

### Stage 1: `materialise_non_daily(user)`

**Purpose**: Push recurring tasks into their corresponding plan files.

**Input**: `Recur/` directory containing YAML or Markdown schedule specs.

**Supported schedule types**: monthly, quarterly, yearly (not daily — daily is handled by stage 2).

**For each recur item**:

1. Parse the schedule spec to determine all due dates that have already passed or are coming up.
2. Determine the target plan file, e.g.:
   - Monthly → `Plans/<OU>/<YYYY>-<MM>.md`
   - Quarterly → `Plans/<OU>/<YYYY>-Q<N>.md`
   - Yearly → `Plans/<OU>/<YYYY>.md`
3. **Idempotency check**: Before inserting, search the target file for the idempotency marker:

   ```
   ^R:<sha1[:8]>-<period>
   ```

   Where `sha1[:8]` is the first 8 chars of SHA1 of the task text, and `period` is e.g. `2026-06`.
   If marker found → skip (already inserted).

4. If not found: append the task bullet + idempotency marker to the plan file.

---

### Stage 2: `materialise_daily(user)`

**Purpose**: Create or update today's Daily note.

**Seeds `Daily/<date>.md` from three sources**:

#### Source 1 — Carry-forward

- Finds the most recent existing Daily file (date before today).
- Reads all lines matching `- [ ] ...` (unchecked tasks).
- These are copied verbatim into today's file as carry-forward items.
- Checked items (`- [x]`) and in-progress items (`- [>]`) are **not** carried forward.

#### Source 2 — Plan pipe (`apply_plan_pipe()`)

```python
def apply_plan_pipe(plans_dir: Path, today: str) -> list[str]:
    ...
```

- Scans all plan files in `Plans/` recursively.
- Finds task bullets containing `start:<today>` or `due:<today>` date tags.
- For each matching task:
  - Rewrites `- [ ]` → `- [>]` in the plan file (marks as "in-progress").
  - Records the task text.
- Returns list of task descriptions that were moved.
- These descriptions are included in today's Daily note as context.

#### Source 3 — Daily checklist

- Reads `Recur/Daily.md`.
- Copies all `- [ ]` items as **fresh unchecked** items (not carried from yesterday — always
  re-inserted from the template each day).

#### Assembly — `build_daily_content()`

```python
def build_daily_content(
    carry_forward: list,
    plan_tasks: list,
    daily_checklist: list,
    date: str,
) -> str:
    ...
```

Pure function. Combines the three source lists into a full Daily note body with proper headings
and formatting. Returns the complete file content string.

---

### Stage 3: `materialise_govern(user)`

**Purpose**: Generate governance and team oversight documents.

**Steps**:

1. Read team-owned recur items (items where the owner/assignee is **not** the current user).
2. Materialise these into `Govern/<YYYY-MM>.md` — a consolidated view of team commitments.
3. Call `materialise_project_playbooks()` — generates/refreshes project playbook documents.
4. Call `rebuild_project_index()` — regenerates a master `Projects/Index.md` from all active projects.

All steps check idempotency markers before writing to avoid duplicate content.

---

### Key Pure Functions

```python
def build_plan_bullet(task: str, date: str, period: str) -> str
```
Constructs a properly-formatted plan bullet string with idempotency marker embedded.

```python
def apply_plan_pipe(plans_dir: Path, today: str) -> list[str]
```
Scans plans, marks due-today tasks as in-progress, returns moved task descriptions. (Detailed above.)

```python
def build_daily_content(carry_forward, plan_tasks, daily_checklist, date) -> str
```
Pure assembly function. No file I/O. (Detailed above.)

---

## 8. `task_queue.py` — SQLite Task Queue

**File path**: `code/backend/task_queue.py`

### Overview

A lightweight, persistent task queue backed by a single SQLite table. Supports async-ish
fire-and-forget tasks (email, Telegram, Jira) with retry and exponential backoff. Deliberately
simple — no Celery, no Redis.

---

### Database Location

```
DATA_ROOT/queue.sqlite3
```

This database is **shared across all users** (unlike per-user SQLite databases for chat history).
The `payload` JSON contains the `username` when user-scoping is needed.

---

### Schema

```sql
CREATE TABLE IF NOT EXISTS task_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type     TEXT    NOT NULL,
    payload       TEXT    NOT NULL,      -- JSON-encoded dict
    status        TEXT    DEFAULT 'pending',
    attempts      INTEGER DEFAULT 0,
    max_attempts  INTEGER DEFAULT 3,
    next_retry_at REAL,                  -- Unix timestamp; NULL means "ready now"
    created_at    REAL    NOT NULL,      -- Unix timestamp
    updated_at    REAL    NOT NULL,      -- Unix timestamp
    result        TEXT,                  -- JSON-encoded result on success
    error         TEXT                   -- error message string on failure
);
```

**Status values**:

| Status       | Meaning                                                  |
|--------------|----------------------------------------------------------|
| `pending`    | Waiting to be picked up                                  |
| `retry`      | Failed, scheduled for retry at `next_retry_at`           |
| `processing` | Currently being executed (set atomically before handler) |
| `done`       | Handler returned successfully                            |
| `failed`     | Exhausted `max_attempts` without success                 |

---

### `enqueue(task_type, payload, max_attempts=3) -> int`

```python
def enqueue(task_type: str, payload: dict, max_attempts: int = 3) -> int:
    ...
```

Inserts a new task row with `status='pending'`. Returns the new `id` (integer primary key).

`payload` is `json.dumps()`-ed before storage.

---

### `drain(batch_size=20) -> dict`

```python
def drain(batch_size: int = 20) -> dict:
    ...
```

The worker loop calls `drain()` on a schedule (e.g. every 30 seconds).

**Steps**:

1. `SELECT` up to `batch_size` rows where:
   - `status IN ('pending', 'retry')` AND
   - `next_retry_at IS NULL OR next_retry_at <= time.time()`
2. For each task:
   a. Set `status = 'processing'`, `updated_at = now()` (atomic UPDATE).
   b. Look up handler in `_handlers[task_type]`.
   c. Call `handler(json.loads(payload))`.
   d. **On success**: `status = 'done'`, store `result = json.dumps(handler_return)`.
   e. **On exception**: increment `attempts`.
      - If `attempts < max_attempts`: `status = 'retry'`, compute `next_retry_at`.
      - If `attempts >= max_attempts`: `status = 'failed'`, store `error = str(exception)`.

**Returns** stats dict:

```python
{
    "processed": int,    # total tasks touched
    "succeeded": int,
    "failed": int,       # terminal failures (exhausted retries)
    "retrying": int,     # scheduled for retry
}
```

---

### Exponential Backoff

```python
BACKOFF_BASE_SEC = 30
```

```
next_retry_at = time.time() + BACKOFF_BASE_SEC × 2^(attempts - 1)
```

| Attempt (just failed) | Wait before next retry |
|-----------------------|------------------------|
| 1                     | 30 s                   |
| 2                     | 60 s                   |
| 3 (max)               | 120 s → then `failed`  |

---

### Handler Registry

```python
_handlers: dict[str, Callable] = {}

def register_handler(task_type: str, handler: Callable) -> None:
    _handlers[task_type] = handler
```

**Registered handlers** (registered at app startup):

| `task_type`    | Purpose                                        |
|----------------|------------------------------------------------|
| `email`        | Send email via Microsoft 365 OAuth             |
| `telegram`     | Send message to Telegram chat                  |
| `jira_create`  | Create Jira issue via REST API                 |
| `news_watch`   | Submit Anthropic batch job for news analysis   |

Each handler receives `payload: dict` and returns a result (any JSON-serialisable value).

---

## 9. `skills.py` — Progressive Disclosure Skills

**File path**: `code/backend/skills.py`

### Overview

Skills are structured workflow guides that the LLM can load on demand. They follow a
"progressive disclosure" pattern: the system prompt always includes a short one-liner manifest
(9 entries, ~200 tokens), but the full multi-page skill content is only loaded when the user
invokes the skill — keeping baseline context lean.

---

### Skill File Format

Skills are Markdown files in `code/src/prompts/skills/` with YAML frontmatter:

```markdown
---
name: daily-review
description: Guide the user through their morning/evening daily review workflow
---

# Daily Review Skill

## Morning Check-in
...

## Evening Wind-down
...
```

- `name` — unique identifier, used in `get_skill_content()` lookups and in the `load_skill`
  LLM tool.
- `description` — one-liner shown in the system prompt manifest. Should be ≤ 80 characters,
  action-oriented.
- Body — full skill content, can be arbitrarily long. Loaded only when invoked.

---

### Built-in Skills (9 total)

| Name                   | Description                                   |
|------------------------|-----------------------------------------------|
| `daily-review`         | Morning/evening daily review workflow         |
| `monthly-planning`     | Monthly planning session                      |
| `quarterly-planning`   | Quarterly OKR/planning session                |
| `weekly-review`        | Weekly retrospective workflow                 |
| `project-setup`        | New project initialization guide              |
| `email-triage`         | Email processing workflow                     |
| `meeting-prep`         | Meeting preparation workflow                  |
| `monthly-compliance`   | Compliance/reporting checklist                |
| `people-delegation`    | Delegation and team management guide          |

---

### `get_manifest() -> list[dict]`

```python
def get_manifest() -> list[dict]:
    ...
```

**Returns**:

```python
[
    {"name": "daily-review",       "description": "..."},
    {"name": "monthly-planning",   "description": "..."},
    # ... all 9 skills
]
```

**Usage**: Called at the start of every `/api/ai/chat` request. The manifest is formatted into
the system prompt so the model knows which skills exist and when to offer them. Including only
names and descriptions keeps the token cost minimal.

---

### `get_skill_content(name: str) -> str`

```python
def get_skill_content(name: str) -> str:
    ...
```

Loads the full body of a skill file (frontmatter stripped).

**Steps**:

1. Locate `SKILLS_DIR / f"{name}.md"`.
2. Read the file.
3. Strip YAML frontmatter (`---` ... `---` block at top).
4. Return remaining Markdown body as string.

Raises `FileNotFoundError` (or equivalent) if `name` does not match any skill file.

**Usage**: Called by the `load_skill` LLM tool handler when the model decides to invoke a skill.
The returned content is fed back to the model as a tool result, giving it the full workflow guide.

---

### LLM Integration Pattern

```
System prompt (every request):
  "Available skills: daily-review (Guide morning/evening review), monthly-planning (...), ..."

User: "Let's do the weekly review."

Model → calls tool: load_skill(name="weekly-review")
Tool result: [full weekly-review.md content]

Model → now has full skill and guides the user through it
```

This pattern avoids loading all 9 × (avg ~2000 token) skills on every request, saving ~15,000
tokens per request while still giving the model access to any skill on demand.

---

*End of `03-backend-modules.md`*
