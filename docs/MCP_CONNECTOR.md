# MCP connector — SLEEPY's to-do front door for external AI apps

*Started 2026-10-26.* Lets an AI app (Claude mobile first; ChatGPT later) work with the
user's to-do list through a **remote MCP server** at `https://klm.smtw.in/mcp`.

## Scope — deliberately narrow

The connector is a **to-do front door, not a second copy of the app.** External apps get a fixed
list of tools and nothing else:

| Tool | Scope | Does |
|---|---|---|
| `get_today` | `tasks:read` | Today's curated task list (`<OU>/Daily/<today>.md` → `## Tasks`) |
| `list_projects` | `tasks:read` | Active project names + `rel_path` (titles only, no body text) |
| `add_task` | `tasks:write` | Add a task to today's list, tagged to an *active* project; idempotent |
| `capture` | `tasks:write` | Append a line to `inbox.md` |
| `toggle_task` | `tasks:write` | Mark done — only a line currently on today's open list |

**Not reachable, by construction (no tool exists):** news feed, morning briefing (embeds inbox text),
file read/write, corpus search, move/delete/rewrite, admin, email. `test_no_file_or_search_tools_exist`
pins that. Every write tool wraps existing `task_scan` code, so path guards, the secret scan and the
`sleepy` git commit still apply.

## Phases

| # | Phase | State |
|---|---|---|
| 1 | Protocol layer (`mcp_server.py`), transport (`mcp_bp.py`), read tools, `MCP_ENABLED` flag (default off) | ✅ 2026-10-26 |
| 2 | Write tools: `add_task`, `capture`, `toggle_task` + hostile-argument handling + write rate limit | ✅ 2026-10-26 |
| 3 | OAuth 2.1 authorization server: metadata, dynamic client registration, consent page, PKCE token endpoint, scoped/revocable tokens. `/mcp` accepts **only** connector tokens; connector tokens are refused on `/api/*` | ✅ 2026-10-26 |
| 4 | Sandbox attack script (`tooling/sandbox/attack_mcp.py`, 140 checks), JSON-RPC batch cap, CLAUDE.md update | ✅ 2026-10-26 |
| — | Deploy (needs the user's go-ahead; canary-gated) and add the connector in claude.ai | pending |


## Design decisions

- **Hand-rolled JSON-RPC, stateless, JSON-only.** No SSE, no sessions. The official SDK is async/ASGI
  and a poor fit for single-worker Flask + gunicorn; the surface we serve (`initialize`, `ping`,
  `tools/list`, `tools/call`) is ~100 lines.
- **Scopes are enforced server-side.** A tool the token's scopes don't cover is absent from
  `tools/list` *and* uncallable by name.
- **Tool output is untrusted-labelled** (`untrusted.mark_untrusted`) like every other path that
  hands corpus text to a model.
- **Origin check.** A request carrying an `Origin` header must be in `MCP_ALLOWED_ORIGINS`
  (default `https://claude.ai`); server-side connector calls send none.
- **Write tools treat arguments as hostile** (the model driving them may have read attacker text
  elsewhere in its chat): input collapsed to one line, `<!-- -->` stripped, `priority:`/`due:`/`^p:`/
  file-path syntax refused in task text, project must be in the active-project list, `toggle_task`
  only accepts a line that `scan_todays_tasks` currently returns, and writes are capped at 60/hour
  (in-process; the backend runs one gunicorn worker).
- **Shared code, not copies:** `capture_to_inbox` and `single_line` moved into `task_scan` so the web
  UI and the connector use identical logic.

## OAuth layer (phase 3)

What the user sees: add the connector in Claude → Claude opens the SLEEPY consent page → sign in with the
normal username/password → tick/untick **Allow changes** → Approve. Claude never sees the password or a
web-session token.

| Piece | Where |
|---|---|
| Rules + storage (PKCE, codes, rotation, revocation) | `oauth_store.py`, tables `oauth_clients/codes/tokens` (migration 11) |
| Discovery, registration, consent page, `/oauth/token`, `/oauth/revoke` | `oauth_bp.py` |
| `/mcp` bearer check + `WWW-Authenticate` pointer | `mcp_bp.py` |
| CLI | `manage_users.py list-connectors` / `revoke-connectors <user>` (reset-password and delete-user also revoke) |

Security properties (each has a test in `tests/test_oauth.py`):

- **Two credential families that cannot be swapped.** Connector tokens are opaque (`sla_…`/`slr_…`), stored
  as sha256 only; the web session is an HS256 JWT. `/mcp` accepts only the former, `/api/*` only the latter.
- **PKCE S256 mandatory**, codes single-use and 60 s; replaying a spent code or a spent refresh token revokes
  the whole grant ("family"). Refresh tokens rotate; scope can narrow on refresh, never widen.
- **Redirect allowlist is the real gatekeeper.** Registration is open (that is how connectors work), so a
  client can only be sent a code at an exact URI from `OAUTH_ALLOWED_REDIRECT_URIS` (default: Claude's two
  callbacks). Unknown client / unregistered redirect → error page, never a redirect. Loopback callbacks only
  with `OAUTH_ALLOW_LOOPBACK_REDIRECTS`, which the config refuses to boot with in prod.
- **Sign-in shares the web login's lockout and `login_events`** — five wrong passwords lock both.
- **Lifetimes:** access 60 min, refresh 30 days (rotating), whole grant 90 days from the password entry,
  then re-approve. Deleting the user or resetting their password revokes everything.
- **Discovery URLs come from `PUBLIC_BASE_URL`**, never the Host header. Prod refuses to boot with
  `MCP_ENABLED` and a non-https base URL.
- **Consent page:** server-rendered, no JavaScript, `frame-ancestors 'none'`, no-store. The final hop back
  to the app is a 200 page with a meta refresh, not a 302 — the site CSP's `form-action 'self'` makes Chrome
  block the redirect that follows a form POST.
- Known trade-off: a refresh response lost in transit makes the retry look like token theft and ends the
  grant (re-approve once). Chosen over a replay grace window.

## Switching it on

1. **Env** *(done 2026-10-26, in `docker-compose.yml`, backend service only)*: `MCP_ENABLED=1`,
   `PUBLIC_BASE_URL=https://klm.smtw.in`. Turn it off again by setting `MCP_ENABLED=0` and redeploying —
   every `/mcp` and `/oauth/*` route then 404s.
2. **nginx** *(done 2026-10-26 on the box)*: the `sleepy_oauth` throttle zone plus `/oauth/`,
   `/.well-known/oauth-*` and `/mcp` locations were spliced into `/etc/nginx/nginx.conf` (backup left at
   `nginx.conf.bak-20261026-*`, `nginx -t` green, reloaded). `tooling/nginx-klm.smtw.in.conf` holds the same
   blocks. Note the box's live config is *older* than that reference file in other respects (no
   `sleepy_login` throttle on `/api/auth/login`, no `client_max_body_size`) — not touched here.
3. **Deploy** through the canary gate (`main` → `canary` → CI green → `prod` → `tooling/deploy-prod.sh`).
4. **Connect:** in the Claude app/site → Settings → Connectors → *Add custom connector* →
   `https://klm.smtw.in/mcp`. Claude opens the SLEEPY consent page; sign in, leave **Allow changes** ticked
   (or untick for read-only), Approve.
5. If the consent page says the redirect address does not match, the app's callback differs from the
   default — add that exact URL to `OAUTH_ALLOWED_REDIRECT_URIS`.

Revoking: `manage_users.py list-connectors` / `revoke-connectors <user>` on the box, or remove the connector
in Claude (it calls `/oauth/revoke`).

## Verification record (2026-10-26)

- Backend suite green (see CLAUDE.md for the count); `tests/test_mcp.py`, `tests/test_oauth.py` cover protocol,
  tools, hostile arguments, the whole OAuth flow, replay/rotation, scope, and token-family separation.
- Sandbox (production posture, throwaway corpus): `attack_http.py` 26 checks / 0 VULN (the connector
  routes are excluded from its "every route needs a token" sweep and covered by `attack_mcp.py`),
  `attack_mcp.py` 140 checks / 0 VULN, run twice back-to-back.
- Found by planning the attacks, fixed before they ran: an unbounded JSON-RPC batch (`MAX_BATCH = 10`).
- Not testable here: the real Claude client and the real nginx. First live connect is the final check —
  if Claude reports a redirect URI problem, add that exact callback to `OAUTH_ALLOWED_REDIRECT_URIS`.
