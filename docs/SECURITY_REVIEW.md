# SLEEPY — Security Review & Hosting Safety Plan

> Full-codebase review performed 2026-07-04 (pre-Phase-6-deploy). Covers every backend
> blueprint, the auth stack, the AI tool loop, `md_editor`, worker, integrations, both
> Dockerfiles, `docker-compose.yml`, the `Caddyfile`, and the frontend token/rendering
> layers. Git history was checked for leaked secrets (clean — only
> `example_secrets_app.py` was ever committed).
>
> **Status legend:** 🔴 must fix before hosting · 🟠 fix before hosting, lower blast
> radius · 🟡 should fix · ✅ verified sound

---

## 1. Security issues to fix before hosting

### 🔴 S1 — Any authenticated Keycloak user becomes owner

**Where:** `code/backend/auth_utils.py:143`

```python
primary_role = app_roles[0] if app_roles else "owner"
```

If a token validates but the user has none of the `OWNER_REALM_ROLES`, `app_roles` is
empty and the code defaults them to **owner** — and `has_perm()` short-circuits to
`True` for owner. Because the shared `Office.smtw.in` realm is reused, any realm user
who can log into the `pma` client gets full access to the entire corpus, email
sending, everything.

**Fix:** return 401/403 when `app_roles` is empty. This is the single most important
fix in the codebase.

### 🔴 S2 — Prompt injection → email exfiltration via the `send_email` tool

**Where:** `code/backend/tools_registry.py:137-141`

The chat LLM can queue an email to **any recipient with any content, no
confirmation**. Its context window contains externally-controlled text: news bullets
that `news_watch.py` pulls from the public web into `inbox.md`, which then flows into
RAG context and the briefing. A crafted news article could instruct the model to
exfiltrate corpus contents by email. Every *other* write action is confirm-gated;
email is the one unguarded side-effect.

**Fix (either or both):**
- Restrict recipients to an allowlist (`config.USER_EMAIL` / `@smtw.in` only).
- Stage emails for user confirmation the same way `write_file` stages edits.

### 🔴 S3 — `/api/auth/config` requires a token, so prod login can never start

**Where:** `code/backend/app.py:74`, `code/backend/auth_utils.py:91`

`validate_token` only skips `/healthz` and OPTIONS. The frontend must fetch
`/api/auth/config` **before** it can initialise Keycloak — with no token yet. In prod
this returns 401 and the login flow deadlocks. Dev bypass masks it today (this is the
"Keycloak path untested" tech-debt item made concrete).

**Fix:** add `/api/auth/config` to the auth-skip list.

### 🟠 S4 — Claude Code OAuth token fallback must not ship to prod

**Where:** `code/backend/config.py:44-79`

`config.py` silently reads `~/.claude/.credentials.json` and uses the personal Claude
Code OAuth token for API calls when no API key is set. Dev-machine convenience hack
only: it ties the server to a personal subscription, breaks on token expiry, and
using the Claude Code token outside Claude Code is against the usage terms.

**Fix:** in prod, require a real `ANTHROPIC_API_KEY` and fail loudly at startup if
the app would fall back to OAuth mode.

### 🟠 S5 — Path traversal in `skills.get_skill_content()`

**Where:** `code/backend/skills.py:32`

```python
path = config.SKILLS_DIR / f"{name}.md"
```

`name` is LLM-controlled (the `load_skill` tool) and unvalidated —
`../../../../data/klm/People` reads any `.md` file anywhere on disk. Impact is
limited (read-only, `.md` only) but it breaks the sandbox every other tool enforces.

**Fix:** reject names containing `/`, `\` or `..`, or resolve and check containment
the way `tools_registry._safe_path` does.

### 🟠 S6 — No guard against `DEV_AUTH_BYPASS=1` in prod

**Where:** `code/backend/config.py:92`

CLAUDE.md rule says "guard it in config" but no guard exists — one mistyped env var
and the whole app is public with owner rights.

**Fix:** hard-fail (raise at import) if `DEV_AUTH_BYPASS` is truthy while
`KEYCLOAK_PUBLIC_URL` is configured, or require a second explicit companion var.

### 🟡 S7 — JWT hardening gaps

**Where:** `code/backend/auth_utils.py:80, 105-128`

- No `iss` (issuer) verification — add
  `issuer=f"{KEYCLOAK_PUBLIC_URL}/realms/{realm}"` to `jwt.decode`. The `azp` check
  helps but issuer pinning is standard belt-and-braces.
- When `KEYCLOAK_HOST_IP` is set, JWKS is fetched over **plain `http://`** — a LAN
  MITM could substitute signing keys. Use HTTPS internally or pin the fetch to the
  Docker network only.

### 🟡 S8 — No request-size or rate limits

`MAX_CONTENT_LENGTH` is unset (unbounded POST bodies straight into the LLM = cost
abuse if a token ever leaks) and there is no rate limiting anywhere.

**Fix:** `app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024` plus Caddy-level rate
limiting — or keep the app VPN-only (see hosting plan Layer 0).

### 🟡 S9 — Minor items

| Item | Where | Fix |
|---|---|---|
| No `Content-Security-Policy` header | `Caddyfile` | Add CSP (`default-src 'self'; connect-src 'self' https://auth.office.smtw.in; ...`) + `Permissions-Policy` |
| `chromadb/chroma:latest` unpinned; Chroma mounts entire `/data` (SQLite + corpus) | `docker-compose.yml:84-94` | Pin the tag; mount only `db/chroma/` |
| Backend container runs as root | `Dockerfile.backend` | Add non-root `USER`; `security_opt: [no-new-privileges:true]` in compose |
| `int(request.args.get("k", 5))` 500s on non-numeric input | `ai_bp.py:220`, `corpus_bp.py:129` | try/except → 400 |
| `capture()` returns raw `str(exc)` on unexpected exceptions | `today_bp.py:153` | Return generic message |
| CORS `supports_credentials=True` unnecessary (Bearer headers, no cookies) | `app.py:15` | Drop it |

### ✅ Verified sound

- `md_editor.validate_path` traversal handling (incl. Windows drive-relative paths)
- `projects_bp` / `logs_bp` path guards; tool-registry `_safe_path` boundary
- DOMPurify on all LLM-rendered surfaces (`mdRender.js`)
- Parameterised SQL everywhere; no string-built queries found
- Secrets never in git history; `secrets_app.py` gitignored, template-only committed
- Confirm-gating on AI edits (`write_file` + `pma-edit` both stage via `propose_edit`)
- PKCE S256 on the Keycloak flow; frontend never parses the JWT

---

## 2. Hosting safety plan (Proxmox deploy)

### Layer 0 — Decide the exposure model first

Single user → the safest option is to **not expose `pa.mspv.app` publicly at all**:
put the VM behind Tailscale/WireGuard (or a Caddy IP-allowlist), keep Let's Encrypt
via DNS-01 challenge. This eliminates ~90% of the attack surface (bots, credential
stuffing, zero-days in Flask/Caddy) at zero feature cost — the PWA works identically
over a tailnet. If it must be public, everything below becomes mandatory rather than
recommended.

### Layer 1 — VM hardening

- `ufw`: allow only 443 (and 80 for ACME) publicly; SSH restricted to LAN/tailnet,
  key-only, root login disabled.
- `fail2ban` on SSH; `unattended-upgrades` enabled.
- Proxmox snapshot before first deploy; snapshot schedule after.

### Layer 2 — Docker hardening

- Only Caddy publishes ports (already true). Put backend/worker/chromadb on an
  `internal: true` network with Caddy bridging.
- Pin all image tags (`caddy:2.x-alpine`, `chromadb/chroma:<version>`,
  `nginx:<version>-alpine`, `node:20.x`); non-root user in `Dockerfile.backend`;
  `security_opt: [no-new-privileges:true]`; `read_only: true` where possible.
- `secrets_app.py` chmod 600 on the VM; keep the `:ro` mount.

### Layer 3 — Keycloak (covers the login + 2FA requirement)

- Fix S1/S3/S7 first — the OIDC path has never run for real.
- In the `Office.smtw.in` realm:
  - **TOTP as a required action** for the user (satisfies the 2FA requirement).
  - `pma` client default-deny for users without the owner role (client-level role
    scoping).
  - Access-token lifetime ≤ 5 min with refresh rotation.
  - Redirect URIs: only `https://pa.mspv.app/*` — no wildcards on other hosts.
  - Brute-force detection ON.

### Layer 4 — App config for prod

- Real `ANTHROPIC_API_KEY` (S4); `DEV_AUTH_BYPASS=0` with the new hard guard (S6);
  CSP header (S9); `MAX_CONTENT_LENGTH` + Caddy rate limit (S8).
- **Gunicorn: 2 sync workers will deadlock under SSE** — one open chat stream
  occupies a worker for its whole life, and the 120 s timeout kills long streams.
  Switch to `--worker-class gthread --threads 8 --timeout 300` (or gevent).

### Layer 5 — Backup & recovery

- Nightly `git push` of `data/klm/` to a private remote (the corpus is already a git
  repo — one cron line, offsite history included).
- Nightly `sqlite3 pma.db ".backup ..."` (WAL-safe) to the same offsite location.
- Chroma is rebuildable — do not back it up.
- **Test a restore once** before calling the deploy done.

### Layer 6 — Monitoring

- External uptime ping on `/healthz`.
- Worker task-failure alert: when a task hits `failed` with
  `attempts == max_attempts`, email the owner (the email handler already exists —
  today failed jobs die silently in SQLite).
- Keep Caddy JSON access logs (already configured); review after the first week.

### Deploy sequence

1. Fix S1–S8.
2. Full test suite green (`tooling/run-backend-tests.bat`).
3. Boot locally with `DEV_AUTH_BYPASS=0` against live Keycloak to shake out the OIDC
   path (S3 surfaces immediately).
4. Deploy behind Tailscale/allowlist.
5. Run for a week; only then consider opening publicly.

---

## 3. Improvement roadmap (beyond security)

### Tier 1 — correctness & robustness (alongside the security fixes)

1. Auth tests with `DEV_AUTH_BYPASS=0` and forged/role-less JWTs — S1 existing with
   138 green tests shows this exact coverage gap.
2. Gunicorn worker-class change (Layer 4) — a functional outage waiting to happen.
3. Task-failure alerting via the existing email handler.
4. Harden the small 500s (non-int query params, capture exception leak).

### Tier 2 — finish what's designed (existing TODO, ordered by value)

5. Phase 6 live deploy, per the plan above.
6. Govern UI (`GET /api/corpus/govern` + `/team` view) — data already generates
   nightly; cheapest feature on the list.
7. `POST /api/corpus/move-line` (inbox → Plan file) — big daily-workflow win.
8. Playbook system (`## Playbook`, `{{token}}`, `^P:` markers).

### Tier 3 — operations & cost

9. LLM spend dashboard: tokens are already logged per event in `ai_events` — add a
   "tokens/cost this week" card and a soft daily budget that pauses non-essential
   jobs (news watch) when exceeded.
10. CI (GitHub Actions): pytest + `npm run build` on every push — enforces
    Development Rule 10 mechanically.
11. `ai_events` growth policy — append-only forever; add a yearly archive/export step
    to housekeeping.
12. Dependency policy: `uv lock --upgrade` monthly + image tag bumps, so patching is
    a habit rather than an emergency.

### Tier 4 — product polish

13. Mobile PWA pass after deploy (offline shell, install prompt).
14. Chat UX: show staged-edit diff cards inline as they stream; "pending edits" badge
    so confirm-gated writes never get lost.
15. Corpus search page (UI over `/api/ai/query`) — endpoint exists, no surface for it.

---

## 4. Addendum — 2026-09-16 re-audit (post AWS deploy, in-app auth)

Full re-read of the repo after the Keycloak → self-issued-JWT switch and the EC2
deploy. Status of the July items: S1/S3/S7 became moot (Keycloak removed); S2, S5,
S6, S8, S9 and the gunicorn item were all found fixed. New findings and what was
done about them, all in the same pass:

| # | Finding | Fix |
|---|---|---|
| A1 | Untracked `tooling/_live_smoke_test.js` held plaintext prod passwords | File deleted; `tooling/_*` gitignored. **Both passwords must be rotated** (`manage_users.py reset-password`) — treat them as burned |
| A2 | Corpus repo `data/klm` tracked `db/` (SQLite with password hashes + chat logs, Chroma, news state), re-committed hourly by `commit_pending` | `db/` untracked + `.gitignore` committed in the corpus repo; `md_editor.ensure_corpus_gitignore()` now runs before every `git add -A` so it can't regress. **History still contains the DB** — purge with `git filter-repo --path db --invert-paths` before any push |
| A3 | `docker-compose.yml` mounted secrets at a path the code never imports | Mount corrected; `.dockerignore` keeps secrets out of the image |
| A4 | Chroma server 0.5.20 vs client 1.5.9 (v1/v2 API mismatch) | Image pinned to 1.5.9 |
| A5 | No `TZ` in containers — "IST" crons fired on UTC | `TZ=Asia/Kolkata` + tzdata |
| A6 | 1 sync gunicorn worker blocked by SSE chat | `gthread` worker class |
| A7 | AI `read_file`/`grep` could read `db/` and non-`.md` files (prompt-injection → hash/chat-log exfil) | Both tools now enforce the same boundary as `md_editor.validate_path`; grep pattern length capped |
| A8 | Lockout keyed on username alone = trivial owner lock-out | Keyed on (username, IP) plus a per-IP ceiling; nginx `limit_req` on `/api/auth/login` |
| A9 | No token revocation (logout no-op, 7-day tokens) | `users.token_version` (migration 6) carried in the JWT; logout and password reset bump it; role now read from DB per request |
| A10 | Timing-based username enumeration | Dummy hash verify on the miss path |
| A11 | `login_events` unbounded, attacker-fed | Username/UA truncated, 90-day prune in housekeeping |
| A12 | `apply_edit` overwrote files changed after proposal | `base_hash` recorded at proposal; mismatch → `EditConflict` → HTTP 409 |
| A13 | Web + worker committed to one git repo without locking | `md_editor.corpus_git_lock()` around every commit site |
| A14 | Crashed tasks stuck in `running` forever | `claim_next` reclaims rows whose `locked_until` passed |
| A15 | Failed handlers' partial DB writes were committed | Worker rolls back before `mark_failed` |
| A16 | `set_status` raw-wrote/removed files, clobbered existing archive targets, crashed on root-level files | Paths validated, existing target refused, root-level refused, under the git lock |
| A17 | Personal Claude Code OAuth token used as an API credential in dev (ToS) | Removed; one API key for every LLM path |
| A18 | Blank `AUTH_SECRET_KEY` only guarded when `APP_ENV=production` | Guarded whenever bypass is off; minimum length 32 |
| A19 | Capture / add-task accepted multi-line text (heading injection) | Collapsed to a single line |
| A20 | `md_indexer.py` `__main__` above its own definitions; `docker-compose.dev.yml` invalid YAML; admin pagination 500 on bad input; Werkzeug debugger on in dev | All fixed |

Still open / by design:
- Dev runs Flask and the worker as two processes on one embedded Chroma directory (Chroma says unsupported). Documented in SETUP.md with the containerised alternative.
- No TOTP second factor. The original requirement was for Keycloak-provided TOTP; with in-app auth it would need its own implementation.
