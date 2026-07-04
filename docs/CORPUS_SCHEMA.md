# SLEEPY — Corpus Schema (Ground Truth)

> **This documents what is actually running today**, verified against the code in `code/backend/` as of 2026-07-04. It is deliberately narrower than `project/05-md-corpus-conventions.md` and `project/17-recurring-tasks-and-progress-tracking.md`, which describe a fuller *target* design from an earlier planning phase — see "Not Yet Built" at the end for what those docs cover that isn't real yet. If this file and `SystemPrompt.MD` (the AI's operational rules) ever disagree, treat `SystemPrompt.MD` as authoritative for AI behavior and file an inconsistency — this doc should always match it.

---

## Directory Structure

```
data/<user>/                       # USER_DATA_ROOT — a git repo of its own
├── ABOUT.md                       # user profile, preferences, working style
├── People.md                      # contacts — one ## section per person
├── inbox.md                       # quick captures, unprocessed items (the safe default)
├── NewsWatch.md                   # standalone news/research interests — one ## section per topic
├── NewsStats.md                   # nightly-generated news activity summary — read-only
├── logs/                          # legacy, superseded by <OU>/Daily/ below
├── db/                            # SQLite + ChromaDB — never touched by AI writes, never in git-tracked content
└── <OU>/                          # one folder per Organisational Unit — a life domain, not a content type
    ├── <project-slug>.md          # project files sit directly in the OU root (flat, no Projects/ subfolder)
    ├── Recur/<name>.md            # recurring task templates
    ├── Daily/<YYYY-MM-DD>.md      # nightly-generated daily task/log file
    ├── Plans/<period>.md          # nightly-generated, monthly/quarterly/yearly recurring rollups
    ├── Govern/<YYYY-MM>.md        # nightly-generated, team-owned recurring items by owner
    ├── People/<nick>.md           # OU-specific contacts (optional — root People.md is the default)
    └── Archive/                   # nightly-generated — completed projects, old daily files
```

**OU naming is a life-domain choice, not a fixed taxonomy.** There is no hardcoded list of valid OU names anywhere in the code — `_find_ous()` (duplicated in `task_scan.py`, `housekeeping.py`, `goal_planner.py`, `materialiser.py`, `news_watch.py`) just lists whatever directories exist. As of 2026-07-04 this user's OUs are:

| OU | Domain |
|---|---|
| `Personal/` | Life goals not tied to college or company |
| `VIT/` | College coursework and capstone projects |
| `SMTW/` | Company work, including building SLEEPY itself |

---

## Project Files (`<OU>/<slug>.md`)

Flat — directly in the OU folder, **not** nested under a `Projects/` subfolder (that's a target-design detail from the reference docs this app doesn't implement).

### Required frontmatter

```yaml
---
key: project-slug        # required — discovery key used by housekeeping/news_watch/goal_planner
status: active            # required — active | on_hold | completed | archived (see below)
owner: KL Mithunvel
started: YYYY-MM-DD       # ISO — see "Date Formats" below
target_date: YYYY-MM-DD   # optional — enables the nightly deadline planner (see below)
news_topics: [topic1, topic2]  # optional — enables nightly news search for this project (always this key, not news_watch:)
---
```

`key` and `status` are required — `housekeeping.py`'s `missing_frontmatter` checker flags files without them. Everything else is optional.

`status` is exactly one of `active | on_hold | completed | archived` (added 2026-07-04, `project_editor.py`'s `set_status`). Setting `archived` physically moves the file from `<OU>/<slug>.md` to `<OU>/Archive/<slug>.md`; setting anything else while the file is in `Archive/` moves it back out. Every other status value is a frontmatter-only rewrite, no move.

### Template body — fixed section order

`## Goal`, `## Why`, `## Current State`, [project-specific custom sections, e.g. a Hardware table], `## Tasks`, `## Decisions`, `## Open Questions`, `## Notes`, `## AI Notes` — see `SystemPrompt.MD`'s "Project File Template" for the canonical example. This order is **fixed** (not just a style preference) as of the 2026-07-04 migration: the Projects view's structured GUI editor (`project_editor.py` + `GET /api/projects/structured`) parses these exact headings into form controls — a status dropdown, editable task rows, and Decisions/Open Questions list editors — so a project file that doesn't follow this shape won't render correctly in the structured editor (it's still viewable/editable via the Raw tab, which just edits the whole file as text).

**Task line tags** — `- [ ] <description> priority:<high|medium|low> due:<YYYY-MM-DD>`, both optional, same style. `priority:` was added 2026-07-04 alongside the structured editor; `due:` already existed (see "Date Formats").

---

## Date Formats

Two different conventions, by field type — this used to be an internal contradiction (fixed 2026-07-02):

- **Frontmatter dates are always ISO `YYYY-MM-DD`** (`target_date`, `started`, `added` on NewsWatch topics, `date` on Daily/Plan files). Every background job that parses frontmatter silently skips a field it can't parse — malformed dates don't error, they just stop working.
- **Task-line `due:`/`start:`/`finish:` tokens** (inside `- [ ]` lines) accept a looser set that `housekeeping.py`'s `invalid_dates` checker validates: `YYYY-MM-DD`, `Mon-DD` (e.g. `Jul-15`), `Mon-YYYY`, `YYYY-MM`, `YYYY`, `Qn`, `YYYY-Qn`.
- Prose/human-facing text may use `DD-MM-YYYY` for readability — never in a field a background job parses.

---

## The Materialiser Pipeline (`materialiser.py`)

Runs nightly at 00:05 IST (worker cron `materialise`), fully deterministic, idempotent — safe to re-run manually via `POST /api/corpus/materialise`.

### Stage 1 — `materialise_non_daily`: Recur → Plans

For each `<OU>/Recur/<name>.md` with `cadence: monthly|quarterly|yearly`: computes the due date from `schedule:`, appends a bullet to `<OU>/Plans/<period>.md` under `## Recurring`, tagged with a `^R:<hash8>-<period>` idempotency marker (SHA-1 of the Recur file's stem). Never re-inserts if the marker is already present.

### Stage 2 — `materialise_daily`: seeds `<OU>/Daily/<today>.md`

Four sources, merged additively (never removes existing lines):

1. **Carry-forward** — unchecked `- [ ]` lines from `## Tasks` in the most recent previous Daily file, prefixed `↳`.
2. **Plan pipe** (`_apply_plan_pipe`) — Plan-file bullets whose `due:`/`start:` token matches today; the Plan-file line is rewritten `[ ]` → `[>]`.
3. **`cadence: daily` / `cadence: weekly` Recur items** (`_daily_tasks`/`_weekly_tasks`) — unconditional every day for `daily`, matching `schedule: weekday:mon` etc. for `weekly`. **`cadence: daily` support was added 2026-07-04** — earlier it silently did nothing; only `weekly` and the separate `Recur/Daily.md` checklist file worked.
4. **`Recur/Daily.md`** (special fixed filename, no frontmatter) — flat checklist, every line becomes a `## Daily checklist` item every day.

First run of the day creates the file fresh; subsequent runs merge only new, not-already-present lines.

### Stage 3 — `materialise_govern`: team-owned Recur → Govern

Recur files where `owners:` is set and doesn't include the current user go to `<OU>/Govern/<YYYY-MM>.md` grouped by owner, instead of the user's own Daily. **Note:** the materialiser side of this is implemented, but there is currently no frontend view or `GET` endpoint to browse Govern files — see "Not Yet Built."

---

## Task-Adding Rules (enforced in `SystemPrompt.MD`)

The Today view / "Active Tasks" panel reads **only** `<OU>/Daily/<today>.md`'s `## Tasks` section (`task_scan.scan_todays_tasks`) — not every open checklist item across every project (that would be `task_scan.scan_open_tasks`, still available but no longer what the UI/briefing use). This means:

- Creating a project with its own `## Tasks` list does **not** put those items in the Today view.
- The AI only appends to a Daily file's `## Tasks` when the user *explicitly* asks to add something to today's list — never as a side effect of any other request.
- A recurring habit ("every day", "once a week") becomes a `<OU>/Recur/<name>.md` template, not a one-off Daily line.
- Telling the AI something is done ("I had that meeting") checks off an existing line — it never creates a new one.

---

## Daily Log Entries (`<OU>/Daily/<today>.md`'s `## Log` section)

Reflective, narrative record-keeping — what happened, distinct from `## Tasks` (actionable, checkable items) and a project's `## Notes` (durable reference context, not date-scoped). Structured as `### Morning`/`### Evening` sub-headings. The AI only adds an entry on explicit request (same discipline as Tasks — see "Task-Adding Rules" above), never as a side effect of a task being mentioned or completed.

The frontend Logs view (`GET /api/logs`, `logs_bp.py`) reads directly from these sections across every OU's `Daily/` folder — there is no separate log file. **The root-level `data/<user>/logs/` folder is legacy and unused** (superseded by this per-OU-Daily convention); if you see references to it in older notes, they're stale.

---

## Standalone News Interests (`NewsWatch.md`)

Root-level file, one `## <Topic>` section with `- added: YYYY-MM-DD`, for interests not tied to a specific project. `news_watch.py` submits nightly searches for both this file's topics and each active project's `news_topics:` frontmatter. Topics older than `config.NEWS_TOPIC_DORMANT_DAYS` (30d) with no `+1` feedback in that window get a stricter "breakthrough only" search instead of being dropped. News items resurface up to `config.NEWS_MAX_RESHOW` (3) times if unclicked, then are permanently excluded; clicking excludes immediately. `NewsStats.md` is a nightly-generated human-readable summary of all this — read-only, not written to by the AI.

---

## Deadline Planning (`goal_planner.py`)

Optional `target_date:` on a project's frontmatter. Nightly (rides the 06:30 IST `morning_briefing` cron): refreshes a `## Plan` section on the project file (LLM-generated, grounded in the project's own content) and folds a deterministic deadline-countdown digest into the emailed morning briefing.

---

## Not Yet Built

The reference docs `project/05-md-corpus-conventions.md` and `project/17-recurring-tasks-and-progress-tracking.md` describe a fuller design. These parts of it are **not implemented**:

- **`<OU>/Projects/<slug>.md` nesting** — this app keeps project files flat at `<OU>/<slug>.md`. Deliberately not adopted (see the 2026-07-04 OU reorg decision — nesting a generic `Projects/` folder inside domain OUs like `VIT/`/`SMTW/` wasn't wanted).
- **Playbook system** (`## Playbook` section embedded in a project file, `@monthly`/`@quarterly`/`@yearly` inline cadence syntax, `{{token}}` substitution, `^P:` idempotency markers) — project-scoped recurring tasks that stay in the project's own `## Tasks` instead of bubbling to Daily. Not built.
- **Govern UI** — `materialise_govern()` already runs and populates `<OU>/Govern/<YYYY-MM>.md`, but there's no `GET /api/corpus/govern` endpoint or frontend `/team` view to browse it yet.
- **`POST /api/corpus/move-line`** — fuzzy-match moving a task line from one file to another (e.g. inbox.md → a Plan file). Not built.

If any of these become real, update this file and note it in `SystemPrompt.MD` too.
