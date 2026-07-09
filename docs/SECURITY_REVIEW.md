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
