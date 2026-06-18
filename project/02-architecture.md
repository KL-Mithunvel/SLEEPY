# PMA — Architecture Reference

**Version:** 0.1.41
**Last updated:** 2026-06-18

This document is the exhaustive architecture reference for PMA (ProjectManagementAssistant). It covers request flows, module responsibilities, data flows for every major operation, the authentication architecture, the AI pipeline, the background worker, the MCP server, and the deployment topology.

Companion documents:
- `01-overview.md` — what PMA is, design philosophy, tech stack, repository layout
- `docs/Project-Charter.MD` — vision, scope, decision log
- `docs/Keycloak-Setup.MD` — Keycloak server configuration

---

## 1. System Architecture Overview

```
Browser
  │
  │ HTTPS
  ▼
Caddy (host, reverse proxy, Let's Encrypt)
  │
  ├── / → pma-frontend:80 (nginx + static Vue assets)
  │
  └── /api/* → pma-backend:5000 (gunicorn + Flask)
               /mcp/*

pma-backend:5000
  ├── uses: Anthropic Claude API (HTTPS, external)
  ├── uses: Keycloak sso.mspv.app (LAN, extra_hosts rewrite)
  └── shares: DATA_ROOT volume with pma-worker

pma-worker (same image as pma-backend)
  ├── APScheduler jobs (index sync, materialise, queue drain, etc.)
  └── uses: DATA_ROOT volume
```

### Production Topology

```
           ┌─────────────────────────────────┐
           │          Caddy (host)            │
           │  pa.mspv.app / sso.mspv.app      │
           └────────────┬────────────────────┘
                        │
          ┌─────────────┴──────────────┐
          │                            │
  ┌───────▼──────┐           ┌─────────▼──────┐
  │ pma-frontend │           │  pma-backend   │
  │ nginx :80    │──/api/*──►│  gunicorn :5000│
  └──────────────┘           └────────┬───────┘
                                      │ shared volume
                             ┌────────▼───────┐
                             │  pma-worker    │
                             │  APScheduler   │
                             └────────┬───────┘
                                      │
                        ┌─────────────▼──────────────┐
                        │  ${DATA_ROOT}  (bind mount) │
                        │  /data/<username>/          │
                        │    md/ (git repo)            │
                        │    db/pma.sqlite3            │
                        │    db/chroma/                │
                        └─────────────────────────────┘
```

---

## 2. Request Flow

### Standard Authenticated Request

```
1. Browser sends:
   GET /api/corpus/file?path=Projects/KILN/ar26.md
   Authorization: Bearer <keycloak-jwt>

2. Caddy receives on pa.mspv.app:443
   → proxies to pma-backend:5000

3. Flask before_request hooks (in order):
   a. _start_timer()         → records g._req_start for slow-request logging
   b. _auth_gate()           → path not in PUBLIC_PATHS and not /mcp
      → calls _resolve_user()
         → auth_utils.extract_bearer(header)   → raw JWT string
         → auth_utils.decode_token(jwt)
            → PyJWKClient fetches JWKS from http://<KEYCLOAK_HOST_IP>:8080/realms/Office/protocol/openid-connect/certs
            → jwt.decode(token, key, algorithms=["RS256"], options={"verify_aud": False}, issuer=<public_realm_url>)
            → checks azp == KEYCLOAK_CLIENT_ID
         → config.cur_user_from_token(claims)
            → strips "@office.smtw.in" from username claim → e.g. "kla"
            → returns CurrentUser(username="kla", email="kla@smtw.in")
         → sets g.cur_user, g.roles

4. Blueprint handler (corpus.py) runs
   → reads g.cur_user.md_root / "Projects/KILN/ar26.md"
   → returns JSON response

5. Flask after_request hook:
   _log_slow() → if elapsed_ms >= SLOW_REQUEST_MS (3000ms), logs WARNING
```

### DEV_AUTH_BYPASS Flow

```
1. DEV_AUTH_BYPASS=1 in environment
2. _resolve_user() skips JWT validation entirely
3. g.cur_user = CurrentUser(username=DEV_USER, email="<DEV_USER>@pma.local")
4. g.roles = ["admin"]
5. X-Dev-User header not required — user comes from DEV_USER env var
```

### Auth for MCP Paths

```
1. Request to /mcp/* or /authorize or /token
2. _auth_gate() sees path.startswith("/mcp") → returns None (skips standard auth)
3. Also PUBLIC_PATHS covers /authorize, /token, /.well-known/*
4. mcp_server.py's _authenticate() runs its own auth:
   → reads X-API-Key header (or Authorization: Bearer)
   → compares to config.MCP_API_KEY using constant-time comparison
   → checks mcp_enabled in DATA_ROOT/<MCP_USER>/settings.json
   → sets g.cur_user = CurrentUser(username=MCP_USER), g.roles = ["admin"]
```

---

## 3. Flask Backend Structure

### Factory Pattern (`app.py`)

```python
def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)

    # Request lifecycle hooks (registered in order)
    @app.before_request
    def _start_timer(): ...          # monotonic clock for slow-request logging

    @app.before_request
    def _auth_gate(): ...            # JWT validation or DEV_AUTH_BYPASS

    @app.after_request
    def _log_slow(response): ...     # warns if elapsed >= SLOW_REQUEST_MS

    @app.teardown_appcontext
    def _teardown(exc): ...          # db.close_all()

    # Error handlers
    @app.errorhandler(404): ...
    @app.errorhandler(500): ...

    # Blueprint registration
    app.register_blueprint(health.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(ai.bp)
    app.register_blueprint(corpus.bp)
    app.register_blueprint(jira_bp.bp)
    mcp_server.init_mcp(app)         # registers both mcp_bp and oauth_bp

    return app
```

### Public Paths (no auth required)

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

All `/mcp` paths also skip standard auth (have own middleware).

### Slow Request Exclusions

These paths are expected to be slow (LLM calls, reindex) — slow-request warnings are suppressed:

```python
_SLOW_SKIP_PATHS = {
    "/api/ai/chat",
    "/api/corpus/reindex",
    "/mcp/call",
}
```

### Blueprint URL Map

| Blueprint | Module | URL prefix |
|---|---|---|
| health | `blueprints/health.py` | `/api/health` |
| auth | `blueprints/auth.py` | `/api/auth` |
| ai | `blueprints/ai.py` | `/api/ai` |
| corpus | `blueprints/corpus.py` | `/api/corpus` |
| jira | `blueprints/jira.py` | `/api/jira` |
| mcp (main) | `blueprints/mcp_server.py` | `/mcp` |
| mcp (oauth) | `blueprints/mcp_server.py` | `/` |

### `CurrentUser` Dataclass (`config.py`)

```python
@dataclass(frozen=True)
class CurrentUser:
    username: str    # e.g. "kla"
    email: str = "" # e.g. "kla@smtw.in"

    @property
    def data_root(self) -> Path:  # DATA_ROOT / "kla"
    @property
    def md_root(self) -> Path:    # DATA_ROOT / "kla" / "md"
    @property
    def db_path(self) -> Path:    # DATA_ROOT / "kla" / "db" / "pma.sqlite3"
    @property
    def v_db_path(self) -> Path:  # DATA_ROOT / "kla" / "db" / "chroma"
```

Available as `g.cur_user` in all request handlers after auth.

---

## 4. Data Flow: Chat Request

The chat endpoint (`POST /api/ai/chat`) is the most complex path in the system. It builds a rich system prompt from multiple sources, performs RAG retrieval, runs the LLM with a tool-use loop, and streams the response as SSE.

### Step 1: Request Reception

```
POST /api/ai/chat
Content-Type: application/json
Authorization: Bearer <jwt>

{
  "messages": [{"role": "user", "content": "What's the status of KILN-AR26?"}],
  "ou": "KILN",
  "purpose_context": "Review meeting prep",
  "gist_summary": "...",
  "toc": [...]
}
```

Response: `Content-Type: text/event-stream` (SSE)

### Step 2: System Prompt Construction

`_build_system_blocks()` in `blueprints/ai.py` assembles a list of system blocks:

```
Block 1: SystemPrompt.MD content
         → hot-reloaded from code/src/prompts/SystemPrompt.MD on every request
         → if file unreadable, logs warning and uses empty string

Block 2: Current context (date/time/OU/user)
         → "Today is Wednesday, 18 June 2026. Time: 10:30 AM. ISO week: 2026-W25. Quarter: Q2."
         → "Current user: KLA (admin)."
         → if ou provided: "Active OU: KILN. Project index at KILN/Projects/Index.md."

Block 3: Active OU context (if ou provided)
         → OU name and instruction to tag items with this OU

Block 4: User's purpose context (if provided)
         → "# User's purpose\n\n<purpose_context>"

Block 5: Corpus gist summary (if provided)
         → "# Corpus gist\n\n<gist_summary>"

Block 6: Corpus TOC (if provided, capped at 2000 chars)
         → file paths + up to 5 top-level headings per file

Block 7: Skills manifest
         → always included: name + one-line description for each of the 9 skills
         → generated by skills.render_manifest_block()
```

`llm._apply_cache_control()` tags **the last block** with `cache_control: {type: "ephemeral"}`.

### Step 3: Chat History Persistence

Before streaming starts, the user's message is persisted to SQLite (`chat_history.py`). This uses standalone connections (not Flask's `g`-managed pool) because the SSE generator runs in a streaming context after the request teardown.

### Step 4: RAG Retrieval

```python
svc = IndexingService(g.cur_user.username)
last_user_msg = messages[-1]["content"]   # last user turn text
include_archive = _wants_archive(last_user_msg)

chunks = svc.get_relevant_context(
    last_user_msg,
    top_k=5,
    include_archive=include_archive,
)
```

`_wants_archive()` uses a regex to detect retrospective phrases:
```python
_RETRO_PHRASE_RE = re.compile(
    r"\b(what did (?:we|i|you)|history of|back in|review the year|"
    r"during 20\d\d|throughout 20\d\d|earlier this|past year|last year|"
    r"previous year|year[- ]end|annual review|in 20\d\d|"
    r"any (?:logs?|notes?|record) (?:about|on|of|from))\b",
    re.IGNORECASE,
)
```

Also triggers on any mention of a past year (e.g. "2025"). When `include_archive=True`, RAG retrieval includes archived Daily files.

Retrieved chunks (up to 4000 chars total) are appended to the last user message as additional context blocks.

### Step 5: LLM Tool Loop

`llm.chat_stream()` is called with:
- `messages` — the full conversation history + RAG-augmented last user turn
- `system` — the system blocks list (with cache control on last block)
- `model` — `claude-sonnet-4-6`
- `tools` — 9 tool definitions

The tool-use loop runs up to `MAX_TOOL_ITERATIONS = 8` times:

```
Iteration N:
  1. Call Claude API (non-streaming for tool iterations, streaming only for final text response)
  2. If stop_reason == "tool_use":
     a. Yield {"type": "tool_start", "name": ..., "input": ...}  → SSE tool_progress event
     b. Execute tool handler (see §7 AI Pipeline for tool details)
     c. Yield {"type": "tool_end", "name": ..., "ok": ..., "error": ...}  → SSE tool_progress event
     d. Append assistant content + tool_result to working_messages
     e. Continue to next iteration
  3. If stop_reason != "tool_use":
     → Stream final text response token by token
     → Yield ChatResult with aggregated usage stats
```

### Step 6: SSE Event Stream

The endpoint yields SSE events as the response:

| Event type | Payload | When |
|---|---|---|
| `delta` | `{"text": "..."}` | Each text token from streaming response |
| `tool_progress` | `{"type": "tool_start", "name": "read_file", "input": {...}}` | Before tool executes |
| `tool_progress` | `{"type": "tool_end", "name": "read_file", "ok": true}` | After tool executes |
| `error` | `{"message": "..."}` | On LLM or tool failure |
| `done` | `{"text": "<full>", "tool_calls": [...], "usage": {...}}` | Final event |

### Step 7: Post-Reply Processing

After the full reply is received, `tools.dispatch()` is called:

```python
actions = tools.dispatch(reply_text, g.cur_user, svc)
```

Currently `TOOL_REGISTRY` contains one handler: `"md-edit": handle_md_edit`.

`handle_md_edit` scans the reply for `pma-edit` fence blocks and applies them (see §5 Data Flow: MD Edit).

---

## 5. Data Flow: MD Edit (pma-edit)

### Edit Block Format

```
```pma-edit
file: Projects/KILN/ar26.md
<<<<<<< SEARCH
- [ ] Review proposal by 2026-06-20
=======
- [x] Review proposal by 2026-06-20
>>>>>>> REPLACE
```
```

### Application Pipeline (`md_patcher.py`)

```
1. extract_edit_blocks(reply_text)
   → EDIT_FENCE_RE finds all ```pma-edit blocks
   → EDIT_BODY_RE parses each: file, search content, replace content

2. For each EditBlock:
   a. Security validation:
      - reject paths containing ".." (path traversal)
      - resolve path must be within md_root
   b. CRLF normalisation:
      - normalise "\r\n" → "\n" in both file content and search string
   c. Match verification:
      - count occurrences of search string in file
      - exactly 0 occurrences → error (SEARCH block not found)
      - more than 1 occurrence → error (ambiguous match)
      - exactly 1 occurrence → proceed
   d. Apply replacement:
      - file_content.replace(search, replace, 1)
   e. Write file to disk
   f. If any edit fails → roll back all previous edits in this batch

3. Commit all edited files as single git commit:
   - Author: ASSISTANT_AUTHOR = Actor("Arivu Baalan", "arivu@smtw.in")
   - Committer: same
   - Message: "AI: <prose_summary>" where prose_summary is extracted from
     the non-fence-block text of the LLM reply
```

### Edit Semantics

| Search | Replace | Result |
|---|---|---|
| Non-empty | Non-empty | Replace matched block in existing file |
| Empty | Non-empty | Create new file with replace content |
| Non-empty | Empty | Delete matched block from file |

### `write_file()` vs `apply_edit()`

`md_patcher.write_file()` is the lower-level function used by the worker (Jira sync, materialiser). It writes a file and stages it but does not commit immediately — commits are batched by `commit_pending_job` (hourly cron).

`apply_reply_if_edit()` (called from AI endpoint) commits immediately.

---

## 6. Data Flow: Indexing

### Trigger

The worker runs `index_sync_job()` every `INDEX_SYNC_INTERVAL_SEC` (default 300s). On worker boot, an initial sync runs immediately before the scheduler loop starts.

### Sync Algorithm (`IndexingService.sync_index()`)

```
1. _discover_users()
   → walks DATA_ROOT
   → returns list of usernames where DATA_ROOT/<user>/md/ exists

2. For each user:
   IndexingService(uid).sync_index()

3. sync_index():
   a. Walk md_root recursively for all *.md files
   b. For each file:
      - get disk mtime (file.stat().st_mtime)
      - query Chroma metadata for stored mtime
      - if mtime changed (or file not in Chroma): mark for upsert
   c. Query Chroma for all stored doc_ids
      - if doc_id not on disk: mark for deletion
   d. Upsert changed files:
      - read file content
      - MarkdownNodeParser.get_nodes_from_documents()
        → splits by heading structure into nodes
        → each node: text content + metadata (file_path, heading, mtime)
      - FastEmbedEmbedding.get_text_embedding_batch()
        → model: BAAI/bge-small-en-v1.5 (ONNX, local, ~100MB)
        → returns float[] vectors (384 dimensions)
      - ChromaDB collection.upsert(ids, embeddings, documents, metadatas)
   e. Delete removed files:
      - ChromaDB collection.delete(ids)
   f. Returns stats: {scanned, upserted, deleted}
```

### ChromaDB Collection

- Collection name: `md_corpus`
- Per-user persistent client at `DATA_ROOT/<user>/db/chroma/`
- Document ID format: `<relative_path>__node_<N>`
- Metadata stored: `file_path`, `heading`, `mtime`

### RAG Query (`IndexingService.get_relevant_context()`)

```python
def get_relevant_context(query: str, top_k: int = 5, include_archive: bool = False) -> list[dict]:
    # 1. Embed query with same fastembed model
    # 2. ChromaDB collection.query(query_embeddings=[...], n_results=top_k)
    # 3. If not include_archive: filter out results from Daily/ subdirectory
    # 4. Return list of {file, text, score} dicts
```

---

## 7. Data Flow: Materialisation (Nightly 00:00)

The materialiser is **entirely deterministic** — no LLM calls. Called by `materialise_job()` in the worker at 00:00 daily.

### Three-Stage Pipeline

#### Stage 1: `materialise_non_daily()` — Recur → Plans

Reads all files in `md_root/Recur/` with frontmatter `cadence: monthly`, `quarterly`, or `yearly`.

For each recur file and applicable period:
1. Compute period key: e.g. `2026-M06`, `2026-Q2`, `2026`
2. Compute stable marker: `^R:<sha1[:8]>-<period>` where sha1 is of the task text
3. Check if marker already exists in the target plan file (idempotent)
4. If not present: append task under `## Recurring` section in the plan file
5. Add concrete `due:` date based on cadence and period

Target files:
- Monthly: `Plans/<OU>/<YYYY>-<MM>-Month.md`
- Quarterly: `Plans/<OU>/<YYYY>-Q<N>.md`
- Yearly: `Plans/<OU>/<YYYY>-Year.md`

#### Stage 2: `materialise_daily()` — Seed Daily/<date>.md

Seeds `Daily/<YYYY-MM-DD>.md` from four sources:

```
Source 1: Carry-forward
  → Read most recent existing Daily file
  → Extract unchecked tasks from "## Tasks" section (lines starting with "- [ ]")
  → Include with [carry-forward] annotation

Source 2: Plan pipe
  → Read all y/q/m plan files
  → Find "- [ ]" lines with "start:<today>" or "due:<today>" attribute
  → Include in today's daily
  → Rewrite matched plan line: "- [ ] " → "- [>] " (marks as scheduled/piped)

Source 3: Weekly recur
  → Read files in Recur/ with cadence=weekly
  → Check if today's weekday matches recur's weekday field
  → Include matching items

Source 4: Daily checklist
  → Read Recur/Daily.md
  → Include all checklist items

Idempotency: each item gets a slug-based marker. Re-running does not duplicate items.
```

#### Stage 3: `materialise_govern()` — Recur → Govern/<YYYY-MM>.md

Reads recur files where `owner` is NOT the current user (delegation tracking).

For each delegated recur:
1. Compute period key: current month
2. Check for stable marker in `Govern/<YYYY-MM>.md`
3. If not present: append under appropriate section

Target file: `Govern/<YYYY-MM>.md`

### Idempotency Markers

```
^R:<sha1[:8]>-<period>
```

Example: `^R:a3f2c1b8-2026-M06`

The sha1 is computed from the task text. The period is the materialisation period. These markers appear as hidden HTML comments or at the end of task lines, ensuring re-runs are safe.

---

## 8. Per-User Data Layout

```
DATA_ROOT/                          # e.g. /data
  queue.sqlite3                     # Shared task queue (keyed by user column)
  <username>/                       # e.g. kla/
    md/                             # Git repo — single source of truth
      Projects/
        <OU>/                       # e.g. KILN/
          <project>.md              # e.g. ar26.md
          Index.md                  # Project index for OU (auto-rebuilt)
      Daily/
        <YYYY-MM-DD>.md             # e.g. 2026-06-18.md
      Plans/
        <OU>/
          <YYYY>-Year.md
          <YYYY>-Q<N>.md            # e.g. 2026-Q2.md
          <YYYY>-<MM>-Month.md      # e.g. 2026-06-Month.md
      Recur/
        Daily.md                    # Daily checklist items
        Weekly.md                   # Weekly cadence items
        <period>.md                 # Monthly, quarterly, yearly recur files
      Govern/
        <YYYY-MM>.md                # e.g. 2026-06.md (delegation governance)
      People.md                     # Team contacts, roles, traits
      Inbox.md                      # Unprocessed quick-captures
      ABOUT.md                      # User profile (seeded from template on onboarding)
    db/
      pma.sqlite3                   # Operational state: chat history, ai_events
      chroma/                       # ChromaDB persistence directory
        chroma.sqlite3              # ChromaDB internal index
        <collection_uuid>/          # Embedding data
```

### Markdown Corpus as Git Repo

The `md/` directory is initialised as a Git repository on user onboarding. Every AI edit is a commit. Git provides:
- Full history of all AI-authored changes
- Authorship attribution (AI vs user)
- Rollback capability
- Diff visibility

The commit messages follow the convention:
- AI edits: `AI: <prose summary from LLM reply>`
- MCP edits: `AI: <summary>` (author email `mcp@smtw.in`)
- User edits (via API): `edit: <summary>` (author: user's Keycloak email)
- Worker/materialiser: `materialise: <description>`

Batch commits from `commit_pending_job` accumulate staged-but-not-committed changes across the hour and commit them together.

---

## 9. Authentication Architecture

### Overview

PMA uses Keycloak as the identity provider with OIDC/OAuth 2.0. The frontend uses keycloak-js for the PKCE S256 flow. The backend validates tokens server-side using PyJWT with JWKS.

```
Browser                    Keycloak                   pma-backend
   │                          │                            │
   │  1. App loads            │                            │
   │  2. keycloak-js init     │                            │
   │  3. redirect to /realms/Office/protocol/openid-connect/auth
   │─────────────────────────►│                            │
   │  4. User authenticates   │                            │
   │◄─────────────────────────│                            │
   │  5. redirect_uri + code  │                            │
   │  6. PKCE token exchange  │                            │
   │─────────────────────────►│                            │
   │  7. id_token + access_token                           │
   │◄─────────────────────────│                            │
   │                                                       │
   │  8. API request + Authorization: Bearer <access_token>│
   │──────────────────────────────────────────────────────►│
   │                          │  9. JWKS fetch (internal)  │
   │                          │◄───────────────────────────│
   │                          │  10. signing key           │
   │                          │───────────────────────────►│
   │                          │  11. jwt.decode()          │
   │  12. API response        │                            │
   │◄──────────────────────────────────────────────────────│
```

### Keycloak Configuration

| Setting | Value |
|---|---|
| Realm | `Office` |
| Client ID | `pma` |
| Flow | Authorization Code + PKCE S256 |
| Token endpoint | `https://sso.mspv.app/realms/Office/protocol/openid-connect/token` |
| Auth endpoint | `https://sso.mspv.app/realms/Office/protocol/openid-connect/auth` |
| JWKS endpoint | `https://sso.mspv.app/realms/Office/protocol/openid-connect/certs` |

### JWT Validation (`auth_utils.py`)

```python
def decode_token(token: str) -> dict:
    """
    RS256 validation with JWKS. One retry on key rotation.
    - verify_aud=False (Keycloak default aud="account")
    - issuer check against public realm URL
    - azp claim must match KEYCLOAK_CLIENT_ID
    """
```

**JWKS URL rewrite (LAN bypass):**

```
Public URL:   https://sso.mspv.app/realms/Office/protocol/openid-connect/certs
Internal URL: http://<KEYCLOAK_HOST_IP>:8080/realms/Office/protocol/openid-connect/certs
```

This rewrite is required because:
- The backend container cannot resolve the public HTTPS certificate for a LAN IP
- DNS hairpin (sso.mspv.app → public IP → NAT → LAN IP) is unreliable in the container network
- The `extra_hosts` in docker-compose maps `sso.mspv.app → KEYCLOAK_HOST_IP` for issuer validation, but JWKS fetch still goes to internal HTTP

**Key rotation handling:**

On `PyJWKClientError`, the JWKS client is evicted from the module-level cache and one retry is made. This handles Keycloak key rotation between calls without requiring a restart.

**Roles extraction:**

```python
def roles_from_claims(claims: dict) -> list[str]:
    roles = set()
    roles.update(claims.get("realm_access", {}).get("roles", []))
    client = config.KEYCLOAK_CLIENT_ID
    roles.update(claims.get("resource_access", {}).get(client, {}).get("roles", []))
    return sorted(roles)
```

**Username extraction:**

```python
# Keycloak sends: username = "kla@office.smtw.in"
# PMA uses only the local part: "kla"
raw = claims.get("username", "")
username = raw.split("@", 1)[0].strip().lower()
```

---

## 10. AI Pipeline

### Model and Parameters

```python
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096
MAX_TOOL_ITERATIONS = 8
TOOL_RESULT_TRUNCATION = 60_000  # chars
```

### Prompt Caching

```python
def _apply_cache_control(blocks: Sequence[dict]) -> list[dict]:
    """Tag the LAST system block with cache_control."""
    out = [dict(b) for b in blocks]
    out[-1] = {**out[-1], "cache_control": {"type": "ephemeral"}}
    return out
```

The ephemeral cache has an approximate 5-minute TTL at Anthropic. The system prompt, corpus context, TOC, and skills manifest are stable across turns in a conversation, so caching them significantly reduces input token costs. `ChatResult` includes `cache_read_tokens` and `cache_creation_tokens` from the API response for observability.

### Tool Definitions

Each tool is a `Tool` dataclass:

```python
@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict    # JSON Schema
    handler: Callable[[dict], str]
```

The 9 chat tools available to the LLM:

| Tool | Handler location | Description |
|---|---|---|
| `load_skill` | `skills.py` | Load a skill file body by name |
| `grep` | `md_grep.py` | Full-text search across corpus |
| `read_file` | `indexing_service.py` | Read a corpus MD file |
| `read_src` | `help_reader.py` / `templates_reader.py` | Read from `code/src/` (help, templates) |
| `list_src` | same | List files in a `code/src/` directory |
| `list_files` | `indexing_service.py` | List files in corpus |
| `search_corpus` | `indexing_service.py` | Semantic RAG query (returns top-K chunks) |
| `send_email` | `task_queue.py` | Enqueue email task |
| `send_telegram` | `task_queue.py` | Enqueue Telegram message task |

### Tool Result Truncation

Tool handlers return strings. If a result exceeds 60,000 characters, it is truncated:

```python
if isinstance(content, str) and len(content) > 60_000:
    content = content[:60_000] + "\n\n[truncated]"
```

This prevents runaway context growth from very large files.

### Streaming vs Non-Streaming Tool Turns

In `chat_stream()`:
- **Tool-use iterations** run as non-streaming `client.messages.create()` calls (invisible to the user except for `tool_progress` SSE events)
- **Final text response** streams token-by-token via `client.messages.stream()`

This means the user sees text appearing in real-time for the final answer, but tool execution happens synchronously before the final stream starts.

### ChatResult

```python
@dataclass
class ChatResult:
    text: str                       # Full final response text
    model: str                      # Actual model used
    input_tokens: int               # Aggregated across all iterations
    output_tokens: int
    cache_read_tokens: int          # Prompt cache hits
    cache_creation_tokens: int      # Prompt cache writes
    stop_reason: str | None
    tool_calls: list[dict]          # [{name, input, ok, error}, ...]
```

---

## 11. Background Worker

The worker runs as a **separate process and container** from the Flask backend. Entry point: `python -m backend.worker`. It uses APScheduler's `BackgroundScheduler`.

### Worker Process Lifecycle

```python
def main():
    signal.signal(signal.SIGTERM, _stop)   # graceful shutdown
    signal.signal(signal.SIGINT, _stop)

    _register_queue_handlers()             # register email/telegram/jira/news_watch handlers

    scheduler = BackgroundScheduler()
    # register all jobs (see table below)
    scheduler.start()

    # Initial sync on boot (before scheduler loop)
    index_sync_job()

    while _running:
        time.sleep(1)

    scheduler.shutdown(wait=False)
```

### Scheduled Jobs

| Job ID | Trigger | Interval/Cron | Function | Notes |
|---|---|---|---|---|
| `index_sync` | interval | every 300s | `index_sync_job()` | Reconciles Chroma with MD files on disk |
| `queue_drain` | interval | every 15s | `queue_drain_job()` | Drains task queue with retry |
| `materialise_daily` | cron | 00:00 | `materialise_job()` | Nightly materialisation pipeline |
| `commit_pending` | cron | every hour (minute=0) | `commit_pending_job()` | Commits staged-but-uncommitted edits |
| `housekeeping` | cron | 23:00 | `housekeeping_job()` | Corpus health checks + archive sweep |
| `news_watch_submit` | cron | 00:00 | `news_watch_submit_job()` | Submit Anthropic Batch API news-watch (disabled if `NEWS_WATCH_CRON_DISABLED`) |
| `news_watch_poll` | interval | every 300s | `news_watch_poll_job()` | Finalize pending news-watch batches |
| `jira_sync` | interval | every 900s | `jira_sync_job()` | Auto-check tasks where Jira status is Done (only if Jira configured) |

All jobs have `max_instances=1, coalesce=True` to prevent overlapping runs.

### User Discovery

```python
def _discover_users() -> list[str]:
    root = config.DATA_ROOT
    if not root.exists():
        return []
    return [p.name for p in root.iterdir() if p.is_dir() and (p / "md").is_dir()]
```

All jobs iterate over discovered users. If a user's job fails, the exception is caught and logged — it does not prevent other users from being processed.

### Task Queue (`task_queue.py`)

The task queue uses SQLite (`queue.sqlite3` at `DATA_ROOT/`) with the following schema (conceptual):

```sql
CREATE TABLE tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type   TEXT NOT NULL,          -- "email", "telegram", "jira_create", "news_watch"
    username    TEXT NOT NULL,
    payload     TEXT NOT NULL,          -- JSON
    status      TEXT DEFAULT "pending", -- "pending", "claimed", "done", "failed"
    attempts    INTEGER DEFAULT 0,
    next_retry  REAL,                   -- Unix timestamp for exponential backoff
    created_at  REAL,
    updated_at  REAL
);
```

`queue_drain_job()` calls `task_queue.drain()`:
1. Claims pending tasks where `next_retry <= now()` and `status = "pending"`
2. Calls registered handler for `task_type`
3. On success: marks `status = "done"`
4. On failure: increments `attempts`, computes exponential backoff, sets `next_retry`, marks `status = "pending"` (for retry) or `"failed"` (if max attempts exceeded)

### Queue Handlers

| Task type | Handler | Action |
|---|---|---|
| `email` | `_handle_email()` | `email_service.send_email()` via MSAL/Graph |
| `telegram` | `_handle_telegram()` | `telegram_service.send_message()` |
| `jira_create` | `_handle_jira_create()` | `jira_service.create_issue()` + write JIRA:KEY back to MD |
| `news_watch` | `_handle_news_watch()` | `news_watch_submit_for_user()` via Anthropic Batch API |

### Jira Sync (`jira_sync_job`)

1. Scans all `.md` files in the user's corpus for unchecked tasks (`- [ ]`) containing `JIRA:<key>`
2. Batches the found keys and queries Jira Cloud via JQL: `key IN (KEY1, KEY2, ...)`
3. For issues with status `done`, `closed`, or `resolved`:
   - Rewrites the task line: `- [ ] ` → `- [x] `
   - Writes the file via `md_patcher.write_file()` (commits on next `commit_pending_job`)

---

## 12. MCP Server

### Protocol

PMA implements the **MCP 2025-06-18** JSON-RPC protocol over Streamable HTTP transport.

```
SERVER_NAME = "PMA"
SERVER_VERSION = "0.2"
MCP_PROTOCOL_VERSION = "2025-06-18"
```

### Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/mcp` | JSON-RPC MCP protocol (initialize, tools/list, tools/call, resources/list, resources/read, prompts/list, prompts/get) |
| GET | `/mcp` | SSE hello (server identification) |
| GET | `/authorize` | OAuth 2.0 authorization endpoint |
| POST | `/token` | OAuth 2.0 token exchange |
| GET | `/.well-known/oauth-authorization-server` | OAuth discovery metadata (public) |
| GET | `/.well-known/oauth-protected-resource` | OAuth resource metadata (public) |

### Authentication

Two auth methods are accepted:

**API Key (direct):**
```
X-API-Key: <MCP_API_KEY>
```
or
```
Authorization: Bearer <MCP_API_KEY>
```

**OAuth 2.0 (for Claude.ai connectors):**
- The `/authorize` and `/token` endpoints implement a simple OAuth code flow
- Client ID: `MCP_OAUTH_CLIENT_ID` (default: `pma-mcp`)
- Client secret: `MCP_OAUTH_CLIENT_SECRET`
- Grants a short-lived access token that is then used as Bearer token for `/mcp`

**MCP enabled check:**

```python
def _is_mcp_enabled() -> bool:
    path = config.DATA_ROOT / config.MCP_USER / "settings.json"
    # reads settings.get("mcp_enabled", False)
```

MCP must be explicitly enabled per user in their settings. Disabled by default.

### MCP Tools (7)

| Tool | Description |
|---|---|
| `read_file` | Read a corpus MD file by relative path |
| `search_corpus` | Semantic search (ChromaDB) |
| `grep` | Full-text regex search |
| `list_files` | List files in corpus (optionally filtered by path prefix) |
| `write_file` | Write/update a corpus file (commits immediately) |
| `apply_edit` | Apply a pma-edit SEARCH/REPLACE block |
| `read_src` | Read from `code/src/` (help, templates) |
| `list_src` | List `code/src/` directory contents |

### MCP Resources (5)

| URI | Content |
|---|---|
| `pma://system-prompt` | Current `SystemPrompt.MD` content |
| `pma://user-profile` | User's `ABOUT.md` |
| `pma://project-index` | Project `Index.md` for the active OU |
| `pma://template-index` | List of available templates in `code/src/templates/user/md/` |
| `pma://skills` | Skills manifest (all skill names + descriptions) |

### MCP Prompts

All 9 skills are exposed as MCP prompts. External LLM clients (e.g. Claude.ai connectors) can request a skill by name via `prompts/get`.

---

## 13. Deployment Architecture

### Docker Compose (3 containers)

```yaml
services:
  backend:
    image: REGISTRY/pma-backend:latest
    container_name: pma-backend
    restart: unless-stopped
    volumes:
      - ./secrets_app.py:/app/backend/secrets_app.py:ro  # secrets never baked into image
      - ${DATA_ROOT}:/data
    environment:
      DATA_ROOT: /data
      CORS_ORIGINS: https://pa.mspv.app
      TZ: Asia/Kolkata
    extra_hosts:
      - "sso.mspv.app:${KEYCLOAK_HOST_IP}"   # LAN IP rewrite for Keycloak JWT validation

  worker:
    image: REGISTRY/pma-backend:latest        # same image as backend
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

### Backend Docker Image (`Dockerfile.backend`)

- Base: Python 3.12 slim
- Installs `code/` with `uv sync --no-dev`
- Copies `code/src/` to `/app/src/` (read-only resources: prompts, templates, help)
- `secrets_app.py` is **not** in the image — mounted at runtime from host
- Default CMD: gunicorn

### Frontend Docker Image (`Dockerfile.frontend`)

- Build stage: Node + npm, runs `vite build` → `dist/`
- Runtime stage: nginx serving `dist/`
- nginx proxies `/api/*` to `pma-backend:5000`

### Caddy Configuration (on host)

Caddy is the public-facing TLS terminator. It is **not** inside the Docker Compose network — it runs on the host and proxies to the frontend container (port 80) and directly to the backend container (port 5000 for `/api` paths if needed).

- Auto-HTTPS via Let's Encrypt for `pa.mspv.app`
- Proxies `/` → `pma-frontend:80`
- Proxies `/api/*` → `pma-backend:5000` (or via frontend nginx)

### Secrets Management

- `secrets_app.py` is gitignored and never committed
- `example_secrets_app.py` is the checked-in template with placeholder values
- At deploy time, `secrets_app.py` is placed in the same directory as `docker-compose.yml` and mounted read-only into both `backend` and `worker` containers at `/app/backend/secrets_app.py`
- `config.py` imports it: `from . import secrets_app` — falls back to env-only if import fails

### Environment Variables in Production

Required at deploy time (set via `docker-compose.yml` `environment:` or `.env` file):

| Variable | Where set | Description |
|---|---|---|
| `DATA_ROOT` | compose env | Host path for user data (mounted as `/data`) |
| `KEYCLOAK_HOST_IP` | compose/secrets | LAN IP of Keycloak host |
| `CORS_ORIGINS` | compose env | `https://pa.mspv.app` |
| `TZ` | compose env | `Asia/Kolkata` |

Secrets (in `secrets_app.py`, not in compose env):

```python
ANTHROPIC_API_KEY = "sk-ant-..."
KEYCLOAK_REALM_URL = "https://sso.mspv.app/realms/Office"
KEYCLOAK_CLIENT_ID = "pma"
KEYCLOAK_HOST_IP = "192.168.x.x"
O365_TENANT_ID = "..."
O365_CLIENT_ID = "..."
O365_CLIENT_SECRET = "..."
O365_MAILBOX = "..."
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_DEFAULT_CHAT_ID = "..."
JIRA_BASE_URL = "https://your-org.atlassian.net"
JIRA_USER_EMAIL = "..."
JIRA_API_TOKEN = "..."
MCP_API_KEY = "..."
MCP_USER = "kla"
MCP_OAUTH_CLIENT_ID = "pma-mcp"
MCP_OAUTH_CLIENT_SECRET = "..."
```

---

## 14. Frontend Architecture

### Bootstrap and Auth

`main.js` bootstraps Keycloak auth before mounting the Vue app:

```javascript
bootstrapAuth()       // initialises keycloak-js, PKCE flow, token refresh
  .then(() => {
    createApp(App).use(router).mount('#app')
  })
  .catch((e) => {
    // render error state without mounting Vue
  })
```

If auth fails (Keycloak unreachable, token invalid), the user sees a static error page with a retry link. The Vue app is never mounted.

### Router

```javascript
routes: [
  { path: '/',        redirect: '/today' },
  { path: '/today',   name: 'today',    component: Today },
  { path: '/q-plan',  name: 'q-plan',   component: QPlan },
  { path: '/projects',name: 'projects', component: Projects },
  { path: '/team',    name: 'team',     component: Team },
  { path: '/govern',  redirect: '/team' },          // legacy
  { path: '/people',  redirect: '/team?tab=people'}, // legacy
  { path: '/files',   name: 'files',    component: Files },
  { path: '/search',  name: 'search',   component: Search },
  { path: '/history', redirect: '/files?tab=history' }, // legacy
  { path: '/settings',name: 'settings', component: Settings },
]
```

### Dev Server Proxy

```javascript
// vite.config.js
server: {
  port: 5173,
  proxy: {
    '/api': 'http://127.0.0.1:5000'
  }
}
```

In development, the frontend at port 5173 proxies all `/api/*` requests to the Flask backend at port 5000. No CORS issues in development.

### Auth Store (`stores/auth.js`)

Wraps keycloak-js. Provides:
- `isAuthenticated` reactive state
- `token` (current access token, auto-refreshed by keycloak-js)
- `username`, `email`, `roles`
- `logout()` function

All API calls include `Authorization: Bearer <token>` via `api.js`.

### Key Components

| Component | Purpose |
|---|---|
| `AppSidebar.vue` | Navigation sidebar with OU selector |
| `ChatPanel.vue` | AI chat interface (SSE streaming, tool progress display) |
| `DiaryPanel.vue` | Today's Daily log editor |
| `InboxPanel.vue` | Inbox.md quick-capture panel |
| `TaskBlocks.vue` | Task list rendering with checkbox toggle |
| `FileTree.vue` | Corpus file browser tree |
| `ProjectForm.vue` | New project creation form |
| `ProjectList.vue` | Project list for active OU |
| `OuSelector.vue` | Dropdown for switching Organisational Units |
| `MoveTool.vue` | Move tasks between files |

---

## 15. Config Module Map

`config.py` is the single source of truth for all configuration. Modules must not read `os.environ` or import `secrets_app` directly.

### Path Resolution (computed at import time)

```python
# __file__ = code/backend/config.py
CODE_ROOT = Path(__file__).resolve().parents[1]   # code/
REPO_ROOT = CODE_ROOT.parent                       # ProjectManagementAssistant/
SRC_ROOT = CODE_ROOT / "src"                       # code/src/
PROMPTS_DIR = SRC_ROOT / "prompts"                 # code/src/prompts/
SKILLS_DIR = PROMPTS_DIR / "skills"                # code/src/prompts/skills/
HELP_ROOT = SRC_ROOT / "help"                      # code/src/help/
TEMPLATES_SEED_ROOT = SRC_ROOT / "templates" / "user"
TEMPLATES_ROOT = TEMPLATES_SEED_ROOT / "md"        # code/src/templates/user/md/
DATA_ROOT = Path(os.environ.get("DATA_ROOT", REPO_ROOT / "data"))
```

In production containers, `CODE_ROOT = /app` (Dockerfile `WORKDIR /app`), so `SRC_ROOT = /app/src` (from `COPY code/src ./src`).

### Config Resolution Priority

```
1. os.environ[key]
2. secrets_app.<key>
3. default value
```

```python
def _get(key: str, default=None):
    if key in os.environ:
        return os.environ[key]
    if secrets_app is not None and hasattr(secrets_app, key):
        return getattr(secrets_app, key)
    return default
```

---

## 16. Error Handling Patterns

### Auth Errors

`AuthError` (subclass of `RuntimeError`) is raised by `auth_utils` on any token failure. The `_auth_gate` hook catches it and returns `{"error": "unauthorised", "detail": "..."}` with HTTP 401.

### LLM Errors

`LLMError` is raised by `llm.py` on Anthropic API failures. The AI blueprint catches it and sends an `error` SSE event to the client.

### Tool Errors

Tool handlers must not raise unhandled exceptions — they should return an error string. The `_run_tool()` function catches all exceptions and returns a `is_error: true` tool result to the LLM. The LLM can then decide how to proceed (retry, inform user, etc.).

### Patch Errors

`PatchError` is raised by `md_patcher` when a pma-edit block cannot be applied (SEARCH not found, ambiguous match, path traversal). The AI blueprint catches it, includes the error in the SSE done event, and does not commit.

### Worker Job Errors

Each job function wraps its per-user iteration in a `try/except Exception`. Failures are logged with `log.exception()` (includes full traceback) but do not abort the job for other users.

### Flask 404/500

Registered in `create_app()`:
- 404 → `{"error": "not found"}`
- 500 → `{"error": "internal error"}`
