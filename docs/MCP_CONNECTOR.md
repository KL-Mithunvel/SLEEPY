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
| `add_task` | `tasks:write` | *(phase 2)* Add a task to today's list, tagged to a project |
| `capture` | `tasks:write` | *(phase 2)* Append a line to `inbox.md` |
| `toggle_task` | `tasks:write` | *(phase 2)* Mark one of today's tasks done |

**Not reachable, by construction (no tool exists):** news feed, morning briefing (embeds inbox text),
file read/write, corpus search, move/delete/rewrite, admin, email. `test_no_file_or_search_tools_exist`
pins that. Every write tool wraps existing `task_scan` code, so path guards, the secret scan and the
`sleepy` git commit still apply.

## Phases

| # | Phase | State |
|---|---|---|
| 1 | Protocol layer (`mcp_server.py`), transport (`mcp_bp.py`), read tools, `MCP_ENABLED` flag (default off) | ✅ 2026-10-26 |
| 2 | Write tools: `add_task`, `capture`, `toggle_task` | |
| 3 | OAuth 2.1 authorization server: metadata, dynamic client registration, consent page, PKCE token endpoint, scoped/revocable tokens. `/mcp` accepts **only** MCP tokens; MCP tokens are refused on `/api/*` | |
| 4 | Sandbox attack scripts for the new surface, setup guide, CLAUDE.md update | |
| — | Deploy (needs the user's go-ahead; canary-gated) and add the connector in claude.ai | |

**Phase 1 must not be deployed with `MCP_ENABLED=1` on its own** — until phase 3, `/mcp` would accept
the app's ordinary session token, which is a broader credential than a phone connector should hold.

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
