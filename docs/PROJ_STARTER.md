# PROJ_STARTER.md

These are kl mithunvel's personal preferences and coding standards for AI-assisted development. When starting a new project, copy the relevant sections verbatim into that project's `CLAUDE.md` under `## User Rules`, below the rules copied from `CLAUDE-COMMON.md`. Add project-specific overrides clearly labelled.

## Software Engineering Preferences

- **DRY (Don't Repeat Yourself):** Extract shared logic into reusable functions. Avoid copy-pasting code blocks.
- **Testing is important:** Write tests for new functionality. Cover the happy path and key failure modes. Use `pytest`; tests live in `tests/`.
- **Consider edge cases:** Think about nulls, empty inputs, boundary values, and concurrent access. Clarify with me if requirements are ambiguous.
- **Explicit over implicit and clever:** Write clear, readable code. Avoid magic numbers, obscure one-liners, and hidden side effects. If someone has to puzzle over what it does, rewrite it.
- **Proper error handling:** Handle errors at the right level. Return meaningful messages. Don't silently swallow exceptions.
- **Deprecation:** Never use deprecated APIs, functions, or modules. If found, rewrite to avoid them after consulting me.

---

## General Principles

- **Simplicity first:** Minimal, straightforward code. No over-engineering.
- **Explain always:** Document your code and decisions. Explain choices and how things work.
- **Backend-heavy:** Prefer logic in the backend; keep frontends thin.

---

## Commit Message Style

Use imperative mood, short subject line (≤ 72 chars), no trailing period:

```
Add CLAUDE.md for project X
Update TODO list after database integration
Fix Key Conventions section for encoding bug
Add Schema Reference for new data source
```

Do not use vague messages like `update`, `fix stuff`, or `changes`.


**NOTE:** Any information in project-specific documents overrides this guide.

---

## Stack

| Layer           | Choice                              | Notes                                            |
|-----------------|-------------------------------------|--------------------------------------------------|
| Backend         | Flask                               | Blueprints per module                            |
| Frontend        | Vanilla JS / lightweight frameworks | Composition API                                  |
| Primary DB      | TimescaleDB (PostgreSQL)            | psycopg2, `%s` placeholders, RealDictCursor      |
| External DB     | MS SQL or remote Postgres           | pymssql / psycopg2, read-only (if applicable)    |
| Auth            | Keycloak                            | OIDC + PKCE, token as Bearer header              |
| JWT             | PyJWT (`pyjwt[crypto]`)             | Not python-jose — unmaintained                   |
| Job queue       | DB-backed `task_queue`              | Replaces Redis for most cases (see below)        |
| Scheduling      | APScheduler → task_queue            | Cron specs in Python, dispatched via queue       |
| Notifications   | Telegram Bot API                    | Group-routed, enqueued via task_queue            |
| Object Storage  | MinIO (S3-compatible)               | Only if large media; otherwise `data/` on disk   |
| Dependency mgr  | uv                                  | Replaces pip + requirements.txt                  |
| Process mgr     | Gunicorn + separate worker          | Worker is a distinct process, NOT inside web     |
| Containers      | Docker Compose                      | One compose file per environment                 |



---

## Project Layout

```
project/
├── backend/
│   ├── .venv/                    # Managed by uv (gitignored)
│   ├── app.py                    # Flask app + blueprint registration + startup recovery
│   ├── config.py                 # Loads secrets_app.py, normalises into DB_CONFIG, etc.
│   ├── auth_utils.py             # RBAC helpers: require_perm, has_perm, compute_permissions
│   ├── config_rbac.py            # RBAC policy: ROLES + PERMISSIONS dict
│   ├── company_settings.py       # Business config — checked in, not secrets
│   ├── local_db.py               # Owned DB: migration engine + pool + advisory-lock singleton
│   ├── db_helpers.py             # row_to_dict, rows_to_list (Decimal/datetime serialisation)
│   ├── <module>.py               # One Blueprint per feature module (thin — dispatch only)
│   ├── <module>_state.py         # State derivation / projections (event-sourced modules)
│   ├── <module>_recording.py     # Validation + mutation (event-sourced modules)
│   ├── worker.py                 # Separate process: APScheduler + task_queue drainer
│   ├── scheduled_tasks.py        # Cron registry: task_type + cron expression
│   ├── task_handlers.py          # Dispatch table: task_type → handler(payload, conn)
│   ├── task_queue.py             # DB-backed queue ops: enqueue, claim_next, mark_done, retry
│   ├── telegram_notifier.py      # send_alert(group_key, html) — enqueues via task_queue
│   ├── sensor_monitor.py         # (domain-specific) background thread started in app.py
│   ├── secrets_*.py              # Credentials — gitignored, hook-blocked
│   ├── example_secrets_*.py      # Templates — checked in
│   ├── pyproject.toml            # Managed by uv
│   ├── data/                     # File uploads — gitignored
│   └── tests/
│       ├── conftest.py           # App fixture + role-specific test clients + DB reset
│       └── test_*.py
├── frontend/
│   ├── src/
│   │   ├── stores/auth.js        # Keycloak init + reactive state
│   │   ├── stores/<domain>.js    # ensureLayout()-style cached singletons
│   │   ├── api.js                # Fetch wrapper — injects Bearer token
│   │   ├── components/layout/    # AppSidebar, AppTopbar
│   │   ├── components/<module>/  # Per-module components
│   │   └── views/                # One view per module
│   └── package.json
├── docs/
│   ├── Schema.md                 # DB setup + annotated schema guide
│   ├── schema.sql                # Full DDL — exported, never hand-edited
│   ├── Kiln-Domain.md / etc.     # Domain knowledge docs
│   └── Module-*.md               # One doc per major module
├── tooling/
│   ├── common/                   # Shared tooling, synced from smtw-common
│   │   ├── .env-common           # GitHub PAT for sync (gitignored)
│   │   ├── release.env           # TEST_CMD, BUILD_SCRIPT, DEPLOY_SCRIPT (gitignored)
│   │   ├── sync_common.py
│   │   ├── bump_ver.py
│   │   └── release.py
│   ├── dev/
│   │   ├── docker-compose.dev.yml          # Dev DB + other services
│   │   ├── replicate_prod_to_local.py      # Lift-and-shift prod → local for testing
│   │   └── prod_readonly.py                # Read-only prod SQL probe
│   ├── run-frontend-tests.bat              # Bat wrappers for Claude Code whitelisting
│   ├── run-frontend-build.bat
│   └── run-prod-query.bat
├── sandbox/                      # One-off scripts (reconcile, manual fixes); gitignored or short-lived
├── VERSION                       # Current version, managed by bump_ver.py
├── scratch/                      # Temp files, gitignored
└── .claude/
    ├── CLAUDE.md                 # Project-specific Claude instructions
    ├── CLAUDE-COMMON.md          # Shared conventions (synced from smtw-common)
    ├── skills/                   # Project-specific Claude skills (e.g. read-prod-data)
    └── plans/                    # Plan-mode artifacts
```

---

## Keycloak Token Validation Pattern

`auth_utils.py` handles JWT validation against Keycloak's JWKS endpoint. Key elements:

### Internal URL for JWKS fetch (`_make_internal_url`)

The backend fetches Keycloak's signing keys server-to-server. In production, Keycloak sits behind a reverse proxy with
an HTTPS cert for the public domain. The backend on the LAN can reach Keycloak directly by IP, but the cert doesn't
cover the LAN IP. Solution: rewrite the public URL to use the LAN IP on HTTP port 8080 for JWKS fetch only.



- `KEYCLOAK_HOST_IP` from `secrets_app.py`. Empty = use public URL (dev).
- Public URL is still used for issuer validation — only JWKS fetch uses internal URL.
- `require_auth(realm_url, client_id, internal_realm_url, dev_bypass=...)` is a factory returning a `before_request`
  handler.

### Dev auth bypass

Set `DEV_AUTH_BYPASS=1` to skip Keycloak entirely in local dev. `require_auth` synthesises a `g.user` with a default
role (typically `admin`). Never enable this in production — guard with environment checks.

### Token decode flow

1. Extract Bearer token from `Authorization` header.
2. Use `jwt.PyJWKClient` to fetch signing key from JWKS endpoint.
3. Decode with `algorithms=["RS256"]`, `verify_aud=False` (Keycloak sets `aud="account"`).
4. Check `azp` claim matches expected `client_id`.
5. On decode failure, evict cached JWKS client and retry once (handles key rotation).
6. Extract roles from both `realm_access.roles` and `resource_access.<client_id>.roles`.

### Admin realm roles

`ADMIN_REALM_ROLES` (e.g. `{"admin", "mspv-apps-admin"}`) maps Keycloak realm roles to the app-level `admin`. Any user
with `mspv-apps-admin` gets full admin access without being listed in `config_rbac.py`.

---

## RBAC Pattern

Core access-control architecture. Apply to every project with authenticated users.

### Files

**`config_rbac.py`** — single source of truth for the access policy.

- `ROLES` tuple, ordered most → least privileged.
- `PERMISSIONS` dict: `'module:action'` → tuple of granted role names.
- Route code references only permission key strings — never role names directly.
- Order permission entries to match sidebar nav order (readability).

**`auth_utils.py`** — three public functions:

- `compute_permissions(roles)` — union of permission keys across all roles; used on login.
- `has_perm(perm_key)` — boolean; for inline field masking inside a route.
- `require_perm(perm_key)` — decorator; gates an entire route; returns 403 if fail.

### Multi-role support

Users may hold multiple roles simultaneously.

- `g.user["roles"]` — list of all matched roles.
- `g.user["role"]` — primary role (most privileged by ROLES order).
- Permissions are the **union** across all roles.
- `has_perm` / `require_perm` check against the full role set.

### Permission key design rules

- Format is always `module:action`.
- `admin` bypasses all checks unconditionally **in code** — never put `admin` in permission tuples.
- `('*',)` means any recognised role passes. Never mix `*` with other roles.
- Fail-closed: unknown permission key grants nobody, including senior roles.
- Three-level read hierarchy for sensitive modules:
    - `module:read_basic` — identity/list fields only.
    - `module:read` — all standard fields.
    - `module:read_senior` — sensitive fields + analytics.

### Frontend auth pattern

`stores/auth.js` holds reactive refs: `authenticated`, `userName`, `userRole`, `userPermissions`.

Init sequence:

1. Fetch Keycloak config from `/api/auth/config`.
2. Init Keycloak with `login-required` and PKCE.
3. On success, call `/api/auth/me` with Bearer token.
4. Store `role` and `permissions` from the response.

The frontend never parses the Keycloak token — it always comes from `/api/auth/me`.

### Sidebar visibility

Each nav item declares minimum permission required. Items are filtered by `userPermissions` — excluded from DOM entirely
when not permitted, never hidden via CSS.

---

## Flask Blueprint Pattern

- One `.py` file per feature module, each exporting a Blueprint.
- All blueprints registered in `app.py` at startup.
- Auth middleware (`@app.before_request`) validates token, populates `g.user`.
- Every non-trivial route has `@require_perm`.
- Prefer **thin blueprints**: route dispatch only. Put validation/mutation in `<module>_recording.py` and state
  projection in `<module>_state.py` when the module is event-sourced or has nontrivial logic.

---

## DB Connection Pattern

### Per-request connection from pool, cached on `g`

```python
def get_db():
    if "db" not in g:
        g.db = get_pooled_db()
    return g.db

@app.teardown_appcontext
def _close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        return_pooled_db(conn)
```

- Use `psycopg2.pool.SimpleConnectionPool`. Size `maxconn >= gunicorn workers + worker process + a few`.
- Start with `maxconn=10` for single-server deployments. Monitor; raise if you see exhaustion.
- For multiple databases (e.g. owned DB + read-only ingest DB), use **separate pools** keyed on distinct config.
- `with conn.cursor() as cur:` for cursors. Rows are plain dicts (RealDictCursor).

### RealDictCursor gotcha

`cur.fetchone()[0]` **fails** — RealDictCursor returns dicts, not tuples. Always use column names:

```python
cur.execute("SELECT version()")
version = cur.fetchone()["version"]
```

### Flask `g` for per-request caching

Beyond DB connections, cache expensive per-request computations on `g` (e.g. `g.zones_cache`). Clear naturally on
request end.

### Timezone policy

**All datetime values in the database are IST (naive). Servers and containers stay UTC.**

| Layer                     | Timezone    | Mechanism                                               |
|---------------------------|-------------|---------------------------------------------------------|
| Server OS / Docker host   | UTC         | Default, untouched                                      |
| Docker containers         | UTC         | No `TZ` env — keeps logs aligned with host              |
| PostgreSQL server (global)| UTC         | Default, untouched                                      |
| PostgreSQL sessions (app) | **IST**     | `SET timezone = 'Asia/Kolkata'` on every conn           |
| DB column type            | `TIMESTAMP` | Naive — stores literal value, no auto-conversion        |
| DB stored values          | **IST**     | `NOW()`, `CURRENT_TIMESTAMP` use session TZ → IST       |
| API responses             | IST+offset  | `row_to_dict()` appends `+05:30` to datetime strings    |
| Frontend display          | As-is       | `DD-MM-YYYY HH:MM`, no TZ suffix                        |
| Python / container logs   | UTC         | From host clock — logs ≠ business data                  |
| Task queue / retry delays | Unix epoch  | `time.time()` — timezone-agnostic                       |

**The boundary:** IST lives inside the DB and everything downstream (API, frontend). UTC lives outside (server OS,
container logs, epochs). `SET timezone` on every pool connection is the single switch point.

**Do NOT set `TZ` on Docker containers** — it splits log timezones between host and container.

**Why `TIMESTAMP` not `TIMESTAMPTZ`**: `TIMESTAMP` stores the literal value without conversion. With all sessions on
IST, values are consistently IST. `TIMESTAMPTZ` adds complexity when every connection agrees on timezone.

---

## Local DB Migration Pattern

- `local_db.py` owns the migration engine and the connection pool.
- `_MIGRATIONS` is an ordered list of `(version, description, [sql_statements])` tuples.
- Each entry is a list of individual SQL strings (not one semicolon-joined string).
- `init_db(config)` at startup applies any pending migrations automatically.
- **Never edit or delete applied migration entries — always append.** Editing a prior migration will desync instances
  that already ran it.
- Use `ADD COLUMN IF NOT EXISTS` and `CREATE TABLE IF NOT EXISTS` for defensive migrations.

### Migration singleton via advisory lock

Multiple gunicorn workers boot concurrently. Guard `init_db` with a PostgreSQL advisory lock so only one worker runs
migrations:

```python
cur.execute("SELECT pg_try_advisory_lock(<MIGRATION_LOCK_ID>)")
if cur.fetchone()["pg_try_advisory_lock"]:
    try:
        _apply_migrations(conn)
    finally:
        cur.execute("SELECT pg_advisory_unlock(<MIGRATION_LOCK_ID>)")
else:
    _wait_for_migrations_to_finish(conn)
```

The same pattern guards singleton worker processes (only one APScheduler / queue drainer).

### Squashing

When the migration list grows long (20+), squash into a single v1 entry with full schema. But only after explicit
confirmation — consider all dev/staging/prod instances. After squashing, dev DBs must be dropped + recreated.

---

## Event-Sourced Module Pattern

For domains where history matters (operations logs, audits, state machines):

- **Immutable event log** is the source of truth. Never UPDATE/DELETE events — add a `voided BOOLEAN` column and
  filter on it.
- **State projection**: `<module>_state.py` replays events to derive current status. Cached per-request on `g`.
- **Validation + recording**: `<module>_recording.py` owns the write path. `record_event(...)` validates (business
  rules, blocking predecessors, state preconditions), inserts, then triggers downstream effects (round advancement,
  trap firing, Telegram alerts).
- **Auto-derived counters**: things like per-entity round numbers advance on specific events (e.g. on UC event).
  Keep the bump logic in one place; never duplicate across call sites.
- Keep projection functions **pure** (conn-in, dict-out) so they can be unit tested and cached freely.

### Event traps (operator-set conditional reminders)

A useful pattern: let operators attach a one-shot reminder to `(entity, future_state, event_type)`. When the matching
event is recorded, send a Telegram message and mark the trap fired. Only mark fired if the send succeeded — otherwise
the next matching event retries.

---

## API Response Conventions

- **Single object:** `jsonify(obj_dict)`
- **List:** `jsonify({'items': [...], 'total': N})` — always wrap, never return bare array
- **Paginated:** `jsonify({'items': [...], 'total': N, 'page': P, 'per_page': PP})`
- **Not found:** `jsonify({'error': '...'}), 404`
- **Forbidden:** handled by `require_perm` → 403
- **Bad request:** `jsonify({'error': 'reason'}), 400`
- **Server error:** `jsonify({'error': 'Internal error'}), 500` — never leak stack traces

Dates: ISO 8601. Decimals: Flask `jsonify` serialises as string (safe). Don't `float()` on the backend.

---

## Task Queue Pattern (DB-backed)

Preferred over Redis for most workloads. Table schema (sketch):

```sql
CREATE TABLE task_queue (
    id SERIAL PRIMARY KEY,
    task_type VARCHAR(50) NOT NULL,
    payload JSONB,
    status VARCHAR(20) DEFAULT 'pending',   -- pending|running|done|failed
    attempts INT DEFAULT 0,
    max_attempts INT DEFAULT 3,
    scheduled_for TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    locked_until TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE INDEX ON task_queue (status, scheduled_for) WHERE status = 'pending';
```

### API (`task_queue.py`)

- `enqueue(task_type, payload, scheduled_for=None)` — INSERT and return id.
- `claim_next(conn, worker_id)` — `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`, set status=running + locked_until.
- `mark_done(conn, task_id)` / `mark_failed(conn, task_id, error, retry_delay)`.

### Dispatcher (`task_handlers.py`)

Dispatch table `task_type → handler(payload, conn)`. Handlers are plain functions, take a connection, commit nothing
(the worker owns the transaction).

### Worker (`worker.py`)

Separate process launched via `python worker.py`. Loop:

1. Acquire advisory lock so only one worker instance runs (production may have multiple nodes).
2. Start APScheduler; schedulers enqueue into `task_queue` at cron time rather than running work inline.
3. Drain loop: `claim_next` → dispatch via handlers → `mark_done`/`mark_failed` with retry backoff.

### Why this beats Redis for small/medium workloads

- One less service to run/monitor.
- Work survives worker restarts naturally.
- SQL introspection: "what's pending?" is a plain SELECT.
- Transactional enqueue with business data in the same commit.

Reach for Redis only when latency or throughput demands it.

### Separate cron-log table? No.

Don't add `scheduled_task_runs` or similar audit table — `task_queue` already records every scheduled run as a row.
Filter by `task_type` + `created_at` for history. Keep the schema minimal.

---

## Telegram Notification Pattern

Centralise in `telegram_notifier.py`. Key elements:

- `TELEGRAM_GROUPS` config dict in `company_settings.py`: `{ "ops": {"name": ..., "description": ..., "chat_id": ...} }`.
- `send_alert(group_key, html_message)` validates the group key and enqueues a `telegram_send` task_queue job. The
  actual Bot API call happens in the worker so a slow/failed send never blocks the request.
- Handler calls Bot API with retry on 5xx + Retry-After respect.
- Use a `threading.Lock` around any in-process alert aggregation to prevent duplicate sends from concurrent threads
  (e.g. a sensor monitor daemon).

### Group routing, not user routing

Route by Telegram **group chat** (engineers group, supervisors group, ops group) not per-user. Joining the group is
the subscription mechanism — no user-management work in the app.

---

## Background Monitoring Daemon

For periodic checks that are lighter than a scheduled task_queue job (e.g. every 15s sensor poll):

- Start a `threading.Thread(daemon=True)` in `app.py` during startup (after DB init).
- Guard startup with an advisory lock so only one gunicorn worker runs the thread.
- Thread owns its own DB connection — do not share with request handlers.
- Use a `threading.Event` for graceful shutdown.
- Persist alert state in the DB so restarts don't re-fire pending alerts.

For jobs that run less often or have heavier logic, prefer the task_queue scheduler instead.

---

## Slow-Request Logger

A simple before/after hook that flags any request exceeding `SLOW_REQUEST_MS` (default 3s). Essential for diagnosing
gunicorn worker timeouts and pool exhaustion.

```python
@app.before_request
def _start_timer(): g._req_start = time.monotonic()

@app.after_request
def _log_slow(response):
    elapsed_ms = int((time.monotonic() - g._req_start) * 1000)
    if elapsed_ms >= SLOW_REQUEST_MS:
        logger.warning("SLOW %d ms  %s %s  status=%s",
                       elapsed_ms, request.method, request.path, response.status_code)
    return response
```

Add this from day one — the cost is negligible and it pays off the first time gunicorn starts SIGKILLing workers.

---

## Replicate Prod → Local Tool

For a backend team needing realistic test data: `tooling/dev/replicate_prod_to_local.py`. Rules learned the hard way:

1. **Full-copy mode is lift-and-shift only** — copy rows verbatim. No "fix-up" steps. If prod has it, local has it.
2. **Cutoff mode** supports "copy events up to date X" for reproducing past states. Any trim / re-seed logic
   (e.g. rebuilding derived counters when events are filtered) runs **only in cutoff mode**.
3. **Safety guards**:
   - Refuse if `local.host != "localhost"`.
   - Refuse if prod and local configs point at the same host+db.
   - Require interactive `[y]` / `[d]` / `[n]` confirmation.
4. **FK-aware copy order**: list parent tables explicitly, copy them first, then remaining tables alphabetically.
5. **Skip list**: tables that shouldn't be copied (time-series hypertables, worker-local queue state, version table).
6. **Sequence reset**: after copy, `setval` on each SERIAL column to `MAX(id)` so future inserts don't collide.
7. Connect with a **read-only user** for the prod side (never the app's write user).

---

## Read-Only Prod Data Skill

For investigating prod without risk:

- Create a `vel_app_readonly` (or similar) PG user with `GRANT SELECT` on app tables.
- Credentials live in `secrets_app.py` as `INGEST_DB_*` or similar prefix (reuse the read-only ingest user if the app
  already has one).
- `tooling/dev/prod_readonly.py` — interactive SQL runner with IST session timezone.
- Wrap it in `tooling/run-prod-query.bat` for Claude Code auto-approve whitelisting.
- Register as a `.claude/skills/read-prod-data/` skill with `SKILL.md` describing the workflow — then Claude invokes
  it naturally when asked to "check prod".

---

## Bat-File Wrappers for Claude Code

Claude Code permission prompts are friendlier against a small set of known commands than against open-ended shell
invocations. Create thin `.bat` wrappers for every command Claude runs frequently:

```
tooling/run-frontend-tests.bat   →  cd frontend && npm run test:unit -- --run
tooling/run-frontend-build.bat   →  cd frontend && npm run build
tooling/run-backend-tests.bat    →  cd backend && uv run pytest
tooling/run-prod-query.bat       →  cd backend && uv run python ../tooling/dev/prod_readonly.py %*
```

Document in `CLAUDE.md`: "always use these wrappers, not direct uv/npm". Whitelist them in Claude Code permission
settings. Noticeable reduction in permission-prompt friction.

---

## Secrets Pattern

- Credentials in `secrets_*.py` files — gitignored and Claude Code hook-blocked.
- Never name a secrets file `secrets.py` — shadows stdlib.
- A corresponding `example_secrets_*.py` is checked in with placeholder values.
- Route all reads through `config.py` — modules import from `config`, never `secrets_app` directly. Makes it easy to
  layer env-var overrides, validate required fields at startup, and keep a single audit point.

---

## Environment & Configuration

Three tiers:

| Tier               | File / Source         | Git status | Examples                                   |
|--------------------|-----------------------|------------|--------------------------------------------|
| Secrets            | `secrets_*.py`        | gitignored | DB passwords, API keys, Telegram bot token |
| Business settings  | `company_settings.py` | checked in | Telegram groups, role lists, limits        |
| Operational config | Environment variables | `.env`     | Debug flag, CORS origins, slow-req ms      |

`debug=True` only in dev. Never hardcode. Vite injects app version — display in sidebar footer.

---

## Versioning & Release

### Version source of truth

`VERSION` at project root. Semantic `MAJOR.MINOR.PATCH`. Vite injects from `VERSION` (or keep `package.json` in sync).

### Release pipeline (`release.py`)

1. Check branch = `main` (override with `-B`).
2. Check clean tree.
3. Run tests (`TEST_CMD`).
4. Bump version (`bump_ver.py`).
5. Commit + tag `vX.Y.Z`.
6. Build (`BUILD_SCRIPT` if set).
7. Deploy (`DEPLOY_SCRIPT` if set).

Usage: `python tooling/common/release.py [-p|-m|-M] [-B]`.

### Shared tooling sync

`tooling/common/sync_common.py` self-updates, then syncs shared files from `smtwkla/smtw-common`:

| Source                              | Destination                      |
|-------------------------------------|----------------------------------|
| `claude-code/CLAUDE-COMMON.md`      | `.claude/CLAUDE-COMMON.md`       |
| `tooling/common/bump_ver.py`        | `tooling/common/bump_ver.py`     |
| `tooling/common/release.py`         | `tooling/common/release.py`      |

---

## Testing Pattern

**Layer 1 — Unit tests (no HTTP, no DB)**

- `config_rbac` structure: keys have colons, roles known, no admin in tuples, wildcards not mixed.
- `compute_permissions`: admin gets all, each role gets expected subset.
- `require_perm` decorator in isolation with a dummy function.
- Multi-role union logic.

**Layer 2 — HTTP enforcement tests**

- Role-specific test clients (fixtures in `conftest.py`).
- Restricted roles → 403 on key writes.
- Privileged roles not blocked (may fail for other reasons, but not 403).

**Layer 3 — Domain tests (event-sourced modules)**

- Seed events via the recording path (not raw INSERTs) so validation runs.
- Assert derived state via the projection functions.
- Use session-scoped `pytest_configure` to truncate the test DB once per run (not per class — that was tried and the
  complexity wasn't worth it). If tests pollute each other, fix the test, not the fixture.

Write `test_rbac.py` first, before any module tests.

---

## Frontend Patterns

### Layout

Fixed sidebar (icons, permission-filtered) + topbar + scrollable main. Dimensions in CSS custom properties on `:root`.

### Navigation hierarchy

- **Level 1** — Sidebar (cross-module).
- **Level 2** — Module toolbar (tabs within a module, local state).
- **Level 3** — Section toolbar (sparingly, when a section has its own sub-views).

Items are permission-filtered; if only one accessible, hide the toolbar.

### Cached domain stores

Use `stores/<domain>.js` for data that's expensive to fetch and rarely changes within a session (e.g. kiln layout).
Export an `ensure<Domain>()` function that caches on first call. Avoids every view re-fetching the same payload.

### API helper

`api.js` is the single point for backend calls. Calls `getToken()` before every request, exports `apiGet`, `apiPost`,
`apiPut`, `apiDelete`. No view calls `fetch` directly.

### Status-dependent views

Render different components per status, not one mega-component with v-if/v-else. Parent loads data, child owns layout
and actions.

### Mutually exclusive action buttons

- Show **Save** when dirty.
- Show **Advance** when clean and preconditions met.
- Never both simultaneously.

### Form validation UX

When the form's submit is disabled, tell the user **why**. Either inline hint text under the problem field, or a
tooltip on the disabled button (`title` attribute bound to a computed `saveDisabledReason`). Silent disabled buttons
are a persistent usability problem.

### Tables, modals, print

Covered in previous starter — unchanged. Server-side paginate. Bootstrap modals for simple forms. `@media print` for
simple print layouts; dedicated route for complex.

### Design tone

personal tool:intenative , dark mode, good looking ui, simple Timestamps `DD-MM-YYYY HH:MM`

---


