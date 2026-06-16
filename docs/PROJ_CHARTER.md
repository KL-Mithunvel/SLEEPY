# PMA (ProjectManagementAssistant) — Project Charter

**Full name:** SLEEPY (EVEN WHEN SLEEPING WORK IS TRACKED AND HANDLED)
**production URL:** run it localy now
**Status:** Internal beta (v0.1)
**Reference docs:** [`Project-Starter.md`](Project-Starter.md) and CLAUDE.md

> \*\*Rule:\*\* This charter overrides `Project-Starter.md` where they disagree. `Project-Starter.md` is the baseline;
> deviations are called out here with a \*\*Why\*\*. The charter is written generically — user-specific identity, org names,
> and personal traits live outside it (assistant system prompt / user profile).

\---

## 1\. Purpose

A calm, purpose-first, self-hosted personal AI project assistant that reconnects day-to-day tasks to the bigger picture.
Markdown + Git stays the durable source of truth; the AI (Claude, via API) reads it, shows "why this task matters",
drafts messages, and safely patches MD files via diff + commit. Usable from mobile, tablet, and desktop.

# calander

**Non-goals:**

* Not another task manager — upstream task systems (Jira, ERPNext, etc.) keep executing tasks.
* No gamification (XP, streaks, badges).
* Not a general-purpose note app — scoped to a single user's project portfolio across their organizations.

\---

## 2\. Scope (v1)

**In scope**

* Today View with top 3–5 tasks + one-line "why it matters".
* Project dashboard: tasks (from MD project files) + linked MD context.
* AI Companion: proactive suggestions with draft-ready artifacts (email body, chat message, MD update diff).
* Safe AI MD editing: LLM returns unified diff → backend patches → Git commit authored as the assistant.
* Nightly jobs: MD re-index, delegatee follow-up reminders.
* Morning briefing + weekly review generators (see §11 Functional Expectations).
* PWA — installable on mobile and tablet.

**Out of scope (v1)**

* Multi-user support. Single-user app. Multi-user deferred.
* Rich text editing in the browser. MD is edited by AI or directly in Git.
* Real-time collaboration / websockets.

\---

## 3\. Architecture

### Data model

* **Markdown files + Git** = single source of truth for context (decisions, plans, reviews, project files, tasks).
* **ChromaDB** = derived MD index; rebuildable from MD repo.
* **SQLite** = app state only (sessions, task\_queue rows, job history, AI-interaction logs). Deletable and rebuildable
except task\_queue history.

### Services (Docker Compose on Proxmox Ubuntu VM)

|Service|Role|
|-|-|
|`backend`|Flask (gunicorn). Routes, auth, MD patch flow, AI proxy.|
|`worker`|Separate process: APScheduler + task\_queue drainer. Never inside web.|
|`frontend`|Vue 3 + Vite SPA, served as static PWA.|
|`keycloak`|OIDC + AD + Passkeys. (wired in at deploy time; `DEV_AUTH_BYPASS=1` for local dev)|
|`caddy`|HTTPS + Let's Encrypt, reverse proxy.|
|`chromadb`|Vector store for MD chunks.|

### Repo layout

* **App repo** — `code/`, `docs/`, `tooling/`, `.claude/`, `docker-compose.yml`, `Caddyfile`. **No user data ever
committed here.**
* **`/data/`** — gitignored mount point at the app-repo root. Mounted as a Docker volume (or bind mount) at deploy.
Holds **one folder per user**.

### Per-user layout (`/data/<USER>/`)

```
data/<USER>/
├── ABOUT.md              # User profile, working style, response preferences
├── People.md             # People the user interacts with — contacts, roles, traits
├── db/                   # Derived state — deletable and rebuildable.
│   ├── sqlite/           # SQLite app state (task\_queue, ai\_events, jira\_issues snapshot, sessions)
│   └── chroma/           # ChromaDB persistence directory for the MD corpus vector index
│
├── <OU-1>/               # One folder per Organizational Unit (e.g., SMTW, MSPVL, Personal)
│   ├── project\_1.md      # Project files live directly inside the OU folder (no domain sub-layer)
│   ├── project\_2.md
│   └── …
├── <OU-2>/
│   └── …
│
├── logs/                 # Daily + weekly logs. Daily: YYYY-MM-DD.md ; Weekly: YYYY-WNN.md
├── archive/              # Completed or shelved projects (mirrors OU subfolders)
└── inbox.md              # Unsorted quick-captures
```

**Rules:**

* The MD files (everything under `/data/<USER>/` except `db/`) are the **single source of truth**. The user's MD folder
may itself be a Git repo (recommended) so every AI edit gets version history.
* `db/` is derived state — deletable and rebuildable from the MD corpus + Jira snapshot.
* Each user is fully isolated: no shared SQLite, no shared ChromaDB.
* Multi-user is deferred operationally, but the layout supports it from day one.

**Why:** one folder per user keeps tenant isolation trivial; placing `db/` next to the user's MD means a full user
backup is a single `rsync`; per-user ChromaDB keeps RAG relevance sharp and avoids cross-leak.

\---

## 4\. Tech Stack — PMA vs Starter baseline

|Layer|PMA|Starter baseline|Deviation?|Why|
|-|-|-|-|-|
|Backend|Flask (3.12+)|Flask|✅ same|—|
|Frontend|**Nuxt 3 (Vue 3)**|Vue 3 + Vite|⚠ deviation|Grok suggestion for PWA/SSR polish. **Tradeoff:** new tooling vs KLA's Vite muscle memory. Candidate revert to Vue 3 + Vite + PWA plugin. **Decide before scaffolding frontend.**|
|Styling|Bootstrap 5|Bootstrap 5|✅ same|—|
|Primary DB|**SQLite**|TimescaleDB/PG|⚠ deviation|Single user, low volume, zero-ops. Swap to Postgres if concurrency or analytics demand. Keep all SQL ANSI-ish to ease migration.|
|Auth|Keycloak + AD + Passkeys|Keycloak|✅ same|Reuse existing Keycloak realm.|
|JWT lib|PyJWT `\[crypto]`|PyJWT|✅ same|—|
|Job queue|DB-backed `task\_queue` in SQLite|DB-backed in PG|✅ same pattern|Schema nearly identical; swap `SERIAL` → `INTEGER PRIMARY KEY AUTOINCREMENT`. No Redis.|
|Scheduling|APScheduler → task\_queue|Same|✅ same|—|
|AI Layer|**LiteLLM + LlamaIndex + ChromaDB**|n/a|➕ new|Multi-model (Claude primary, Grok fallback). Index MD for RAG.|
|Git ops|**GitPython**|n/a|➕ new|Apply AI diffs, commit as `Arivu Baalan <arivu@smtw.in>`, push to Gitea.|
|HTTPS|Caddy|(implicit reverse proxy)|✅ compatible|Auto Let's Encrypt for the public domain.|
|Notifications|Telegram primary, Email, (WhatsApp later)|Telegram|✅ same + extension|Use `telegram\_notifier.py` pattern; enqueue via task\_queue.|
|Dep manager|uv|uv|✅ same|Already configured in `code/pyproject.toml`.|
|Process mgr|Gunicorn + separate worker|Same|✅ same|—|
|Containers|Docker Compose|Same|✅ same|—|

\---

## 5\. Patterns adopted verbatim from `Project-Starter.md`

Apply these without deviation:

* **RBAC skeleton** even with single user — future-proof, avoids a rewrite later. `config\_rbac.py` with a single `owner`
role that has everything. Add more roles only when a second user exists.
* **Secrets pattern**: `secrets\_app.py` gitignored; `example\_secrets\_app.py` checked in; route reads through
`config.py`. Never name a file `secrets.py`.
* **DB migration pattern**: ordered `\_MIGRATIONS` list, `ADD COLUMN IF NOT EXISTS`, append-only. Advisory lock — SQLite
equivalent is a startup file lock or single-writer convention (backend owns schema, worker never migrates).
* **Flask `g` per-request DB caching** + teardown return to pool.
* **API response conventions**: `{items, total}` wrappers, ISO 8601 dates, Decimal as string via `jsonify`.
* **Task queue pattern**: same schema, same claim/done/retry API. Worker owns txn boundary. One advisory lock → SQLite
single-worker is fine at this scale.
* **Slow-request logger** from day one.
* **Keycloak token validation** with internal-URL rewrite for JWKS + dev bypass env flag.
* **Bat-file wrappers** for every command Claude Code runs (`run-backend-tests.bat`, `run-frontend-build.bat`,
`run-md-index.bat`).
* **Testing layers**: unit (RBAC), HTTP (role enforcement), domain (event-sourced modules). `test\_rbac.py` first.
* **Release pipeline** via `tooling/common/release.py` + `VERSION` file from day one.
* **Timezone policy**: IST inside DB + API; UTC outside (OS, container logs). `SET timezone` on every SQLite session via
`PRAGMA` is n/a — SQLite stores text; store ISO-8601 IST strings, convert at boundaries.
* **Event-sourced module** for AI interactions + MD-edit history (immutable log; never UPDATE/DELETE; `voided BOOLEAN`).
Every AI-proposed diff, accepted or rejected, is a row.

\---

## 6\. PMA-specific patterns (new)

### 6.1 Safe AI MD editing (core flow)

1. User triggers action → backend builds prompt with relevant MD sections via LlamaIndex retrieval.
2. LiteLLM calls Claude (primary) → returns **unified diff** against one or more MD files.
3. Backend validates diff: file paths inside the user's folder `data/<USER>/` (excluding `db/`), no binary files, no >
N-line changes without confirmation.
4. Backend applies diff to a working copy; runs MD sanity check (frontmatter intact, no broken headings).
5. **User confirmation gate** for any non-trivial edit (configurable threshold).
6. Commit via GitPython with message `AI: <summary>`; author `Arivu Baalan <arivu@smtw.in>`; push to Gitea.
7. Log event in `ai\_events` table: prompt hash, model, diff, accepted/rejected, latency, token count.

### 6.2 Jira sync

* Pull via JQL per project. Cache in SQLite `jira\_issues` (snapshot, not SOT).
* Nightly full sync; on-demand per-project sync via UI button.
* Link Jira ticket ↔ MD project file via explicit key in MD frontmatter (`jira\_project: SMTW`).

### 6.3 "Why this matters" generation

* Per-task one-liner, generated lazily on first view and cached per `(task\_id, MD-file hash)`.
* Invalidate on MD file change or task update.
* Display on Today View, Project Dashboard, notifications.

### 6.4 Proactive suggestions

* Runs as scheduled `task\_queue` job (morning + evening).
* LiteLLM prompt includes: today's daily log, last 7 days of logs, active project files, overdue Jira tasks.
* Output: 0–3 suggested actions with draft-ready artifacts (email, Telegram msg, MD update diff).
* User approves → action executed → logged.

### 6.5 MD indexing

* LlamaIndex + ChromaDB.
* Reindex triggered by: (a) Git post-commit hook, (b) nightly full reindex, (c) manual "/reindex" via UI.
* Chunk by heading hierarchy; preserve frontmatter as metadata.

\---

## 7\. Security

* Keycloak-protected. No anonymous routes except `/healthz`.
* Passkey-first; AD-backed password fallback.
* AI provider keys in `secrets\_app.py`; never sent to frontend.
* Claude/Grok calls redact `secrets\_\*.py`, `.env\*` paths before including any file content.
* Git push uses deploy key for Gitea; key stored outside app image, mounted at runtime.
* No secrets in MD files — enforce with a pre-commit check in the MD repo.
* HTTPS-only via Caddy; HSTS enabled; mobile clients never see HTTP.

\---

## 8\. Architectural decisions

### Resolved

1. **Frontend:** Vue 3 + Vite (no Nuxt — SSR not needed). *(decided 2026-04)*
2. **Repo:** Split — app repo + MD corpus as separate git repo mounted at `data/<user>/md/`. *(decided 2026-04)*
3. **Dev/runtime context docs:** `CLAUDE.md` (Claude Code dev instructions) + `BOT.md` (runtime assistant persona) +
`SystemPrompt.MD` (hot-reloaded per request). No separate KLA-AI-Context needed. *(decided 2026-04)*
4. **Keycloak realm:** Reuse existing `Office.smtw.in` realm with a `pma` public client (PKCE S256). *(decided 2026-04)*
5. **MD repo hosting:** Local git only — no remote push. Git provides version history and rollback; VM infra backup
covers disaster recovery. No Gitea/GitHub needed. *(decided 2026-04)*
6. **Mobile access off-LAN:** Public HTTPS via existing nginx + Certbot infra (`\*.mspv.app`). New site config
`pa.mspv.app.conf` proxying to PMA containers. Same pattern as other `mspv.app` services. Keycloak SSO already in
place. *(decided 2026-04)*
7. **AI quotas / cost guardrails:** Deferred. Platform-level alerts configured at platform.claude.com. In-app token
tracking and budget enforcement not needed for single-user v1 — revisit if multi-user or costs escalate. *(decided
2026-04)*

All architectural decisions resolved.

\---

## 9\. Success criteria (v1 done-definition)

* KLA uses PMA daily on iPhone for at least 2 weeks without falling back to CLI/Claude Desktop.
* Morning briefing is generated automatically and delivered via Telegram before 07:00 IST.
* At least 80% of AI-proposed MD edits are accepted without manual correction.
* MD repo history shows only clean AI commits; no binary corruption, no frontmatter breakage.
* Jira sync lag < 15 min during working hours.
* P95 page load on iPhone over cellular < 2 s.

\---

## 10\. Bootstrap checklist

Adapted from `Project-Starter.md §Checklist`:

1. Resolve the 3 remaining open decisions above (§8).
2. Create repos (split or mono) and move current content.
3. `uv init backend \&\& uv add flask flask-cors pyjwt\[crypto] apscheduler gitpython litellm llama-index chromadb`.
4. Scaffold frontend (Nuxt or Vite — per decision #1) + Bootstrap 5 + Keycloak JS.
5. Copy skeletons from prior projects: `config\_rbac.py`, `auth\_utils.py`, `local\_db.py` (SQLite variant),
`db\_helpers.py`, `config.py`, `task\_queue.py`, `worker.py`.
6. `docker-compose.dev.yml` with Keycloak + ChromaDB + Caddy.
7. `example\_secrets\_app.py` + `.gitignore` for `secrets\_\*.py`.
8. `test\_rbac.py` before any module tests.
9. `.bat` wrappers for test / build / reindex.
10. Slow-request logger in `app.py`.
11. First migration: `db\_version`, `ai\_events`, `task\_queue`, `jira\_issues`, `md\_chunks\_meta`.
12. First release `release.py -p` to validate end-to-end before real features.
13. Update `.claude/CLAUDE.md` to point to this charter + Project-Starter + domain docs.

\---

## 11\. Functional Expectations

The AI assistant operates over a structured MD corpus. The backend must expose these capabilities as first-class
features (UI + API + scheduled jobs).

### 11.1 Project \& Todo Management

* All project state lives in Markdown files under `<org>/<domain>/<project>/`.
* Each project folder contains its own project MD file(s) (typically `<project>.md`; optionally `README.md`,
`NOTES.md`).
* The assistant can read, summarize, update, and reorganize tasks across files.
* Consolidated views (cross-project, cross-org) are generated on demand, never stored as SOT.

### 11.2 Morning Briefing

Triggered via UI button, scheduled cron, or chat phrase. Steps:

1. Read today's daily log; create from template if missing.
2. Scan all active project files (`Status: Active`) for tasks due today or overdue.
3. Check `inbox.md` for unsorted items.
4. Summarize what's due, overdue, blocked, or needs a decision.
5. Suggest a focus plan — **no more than 3 priority items**.
6. Deliver via UI + Telegram before the user's configured start-of-day time.

### 11.3 Weekly Review

Triggered manually or scheduled for end-of-week. Steps:

1. Scan all project files for changes this week (tasks completed, decisions made).
2. Identify stuck projects — no task movement in 7+ days.
3. Generate a weekly review file from template.
4. Surface deadlines in the next 2 weeks.

### 11.4 Task Capture

Quick-capture via UI, chat, or voice. Steps:

1. If a project is obvious from context, append the task to that project file.
2. Otherwise append to `inbox.md` with an ISO-8601 timestamp.
3. Confirm what was added and where.

### 11.5 Project Health Check

Returns, across all active projects:

* Projects with overdue tasks.
* Projects with no movement in 14+ days.
* Projects with open blockers.
* Projects with no target date set.

Tone: honest, not sugar-coated. Stalled projects are flagged, not hidden.

### 11.6 Delegation Support

* When a task is delegated, record in the project file with `@owner` and assigned date.
* Daily/weekly views surface delegated tasks with no updates since assignment.
* On request, draft follow-up messages (email, Telegram, WhatsApp) for pending delegated work.

### 11.7 Email Support \& Integration

* Scan inbox for emails related to active projects.
* Turn action items into tasks in the correct project MD file.
* Summarize important emails grouped by project.
* Flag emails that need action; associate with project.
* Draft replies matching the user's configured tone; never send without explicit confirmation.
* Track payment-related emails (banking, vendor invoices) under the relevant project or daily log.
* Integrations via MCP (Gmail) and/or Microsoft Graph API.

### 11.8 Other Standing Capabilities

* Progress reports on demand.
* Spot bottlenecks across projects.
* Suggest workflow improvements.
* Generate cross-org synthesis views when asked.

\---

## 12\. MD Corpus Conventions

### 12.1 Folder Structure (per user)

```
data/<USER>/
├── ABOUT.md                # User profile
├── People.md               # All contacts (reportees, vendors, peers) in one file
├── db/
│   ├── sqlite/
│   └── chroma/
│
├── <OU-1>/                 # One folder per OU (e.g., SMTW, MSPVL, Personal)
│   ├── project\_1.md        # Project files live directly in the OU folder — no domain sub-layer
│   └── project\_2.md
├── <OU-2>/
│   └── …
│
├── logs/                   # Daily logs (YYYY-MM-DD.md) + weekly reviews (YYYY-WNN.md) in one folder
├── archive/                # Completed or shelved projects (same OU subfolders)
└── inbox.md                # Unsorted quick-captures
```

**Templates** live in the app repo at `code/src/templates/` (project, daily, weekly, People entry). They are shipped
with the app, not per-user. The bot reads them to scaffold new files inside `data/<USER>/`.

```

Hierarchy: \*\*user / OU / project\*\*. Project MD files live directly inside the OU folder. No nested domain layer — if a domain split is useful inside a large OU, express it via filename prefix (`sales-campaign-q3.md`) or frontmatter tag, not subfolders.

\*\*People.md\*\* is a single file with a section per person (see §12.5). Each project references people via `@name`; the assistant resolves `@name` against `People.md`.

### 12.2 Project File Template
```markdown
# <Project Name>

\*\*Status:\*\* Active | On Hold | Blocked | Completed
\*\*Priority:\*\* P1 (urgent) | P2 (important) | P3 (when possible) | P4 (someday)
\*\*OU:\*\* <OU>
\*\*Started:\*\* YYYY-MM-DD
\*\*Target:\*\* YYYY-MM-DD or Quarter (e.g., Q2 2026)
\*\*Depends on:\*\* \[other project names, if any]
\*\*Blocks:\*\* \[projects this unblocks when done]

## Objective
One or two sentences — what does "done" look like?

## Current State
What's the situation right now? Last meaningful update.

## Tasks
- \[ ] Task description — @owner if delegated — due:YYYY-MM-DD if time-bound
- \[x] Completed task — done:YYYY-MM-DD
- \[-] Cancelled task — reason

## Decisions
- YYYY-MM-DD: What was decided and why

## Open Questions
- Things that need answers before progress can continue

## Log
- YYYY-MM-DD: What was done — brief description of action taken or progress made

## Notes
Free-form observations, links, references.
```

### 12.3 Daily Log Template (`logs/YYYY-MM-DD.md`)

```markdown
# YYYY-MM-DD — Day of Week

## Plan (morning)

## Done

## Interruptions / Unplanned

## Carry Forward

## Email Actions
```

### 12.4 Weekly Review Template (`logs/YYYY-WNN.md`)

```markdown
# Week NN — YYYY

## Wins

## Stuck

## Next Week Focus

## Project Status Snapshot

## Delegation Check
```

### 12.5 People.md Template

Single file; one level-2 heading (`##`) per person. The assistant resolves `@name` mentions in project files against
these headings.

```markdown
# People

## <Name>

- \*\*Role:\*\* <role / title>
- \*\*OU:\*\* <OU>
- \*\*Relationship:\*\* Reportee | Peer | Vendor | Client | …
- \*\*Contact:\*\* email / phone / telegram handle
- \*\*Responsibilities:\*\* What they own.
- \*\*Traits:\*\* How to work with them — strengths, quirks, tone to use.
- \*\*Notes:\*\* Free-form context.

## <Next Name>

…
```

### 12.6 Task Syntax

* GitHub-style checklists: `- \[ ]` open, `- \[x]` done, `- \[-]` cancelled.
* Owner: `@name` (resolves to a `## <name>` section in `People.md`).
* Due date: `due:YYYY-MM-DD`.
* Completed date: `done:YYYY-MM-DD`.
* Priority label inline (when overriding project default): `P1`/`P2`/`P3`/`P4` or `HIGH`/`MEDIUM`/`LOW`.

### 12.7 Priority Conventions

|Tag|Meaning|Equivalent|
|-|-|-|
|`HIGH`|today or tomorrow|P1|
|`MEDIUM`|this week|P2|
|`LOW`|next 30 days|P3|
|—|someday / backlog|P4|

### 12.8 Writing Style the Assistant Must Enforce

* Professional, warm, direct.
* Action-oriented and concise; no filler.
* Clean Markdown formatting — headings, checklists, tables.
* Always include deadlines and responsible person when knowable.
* Proactive: suggest next actions, flag risks.
* Keep language simple; match the user's configured locale/mix.

### 12.9 Guardrails

* Do not invent projects, tasks, or people. Only work with what's in the files or what the user states.
* Do not send emails, chat messages, or external notifications without explicit user confirmation. Draft first, send on
command.
* Do not echo the user's own message back. Acknowledge and act.
* Do not edit MD files silently — every AI edit goes through the diff/commit flow (§6.1).

