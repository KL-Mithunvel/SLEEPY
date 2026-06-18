# MD Corpus Conventions

## Overview

The MD corpus is the single source of truth for all user project content in PMA. It lives in a separate git repository — completely independent of the PMA application code repo. This separation means:

- User data is never inside the app code repo. No accidental commits of private content.
- The MD repo can be backed up, inspected, or migrated with standard git tools independent of the app.
- Every AI-authored and user-authored edit is committed to git for complete audit history and rollback capability.

Each user's corpus root is located at `DATA_ROOT/<username>/md/`. The `md/` directory is itself a git repository, initialised automatically by the app on first write if it does not already exist.

The corpus is intentionally plain Markdown — no proprietary database formats, no binary files. A user can edit files with any text editor or via git and the app picks up changes on the next index sync.

---

## Directory Structure

```
md/                                    # root of the corpus git repo (DATA_ROOT/<user>/md/)
  <OU-Name>/                           # one top-level folder per Organizational Unit
    Projects/
      <project-name>.md                # one file per project
      Index.md                         # auto-generated project index (do not edit manually)
    Daily/
      <YYYY-MM-DD>.md                  # one file per day
    Plans/
      <YYYY>.md                        # annual plan
      <YYYY>-Q<1-4>.md                 # quarterly plan
      <YYYY>-<MM>.md                   # monthly plan (e.g. 2026-06.md)
    Recur/
      Daily.md                         # daily checklist items (no YAML frontmatter)
      <recur-name>.md                  # each recurring task gets its own file with YAML frontmatter
    Govern/
      <YYYY-MM>.md                     # governance tracking for team-delegated tasks (per month)
    Playbook/                          # (optional) standalone playbook files (see Playbook Format)
  People.md                            # contacts and team member profiles (single file, all people)
  Inbox.md                             # unprocessed quick-captures
  <anything-else>.md                   # other files allowed at root
```

**OU (Organizational Unit):** Each top-level folder under `md/` is an OU. Examples: `ACME`, `INFRA`, `Personal`, `HRAdmin`. Users typically have 3–8 OUs. The OU name becomes a metadata field in ChromaDB for scoped RAG queries.

**Key design rule:** Every piece of user-generated content (projects, daily logs, recurring tasks, governance) lives inside a named OU. Only `People.md` and `Inbox.md` live at the corpus root because they are cross-OU by nature.

---

## Project Files (`<OU>/Projects/<project-name>.md`)

Project files are the primary content type. One file per project. The filename becomes the project's slug (used in index and cross-references).

### YAML Frontmatter

Project files use YAML frontmatter for structured metadata:

```yaml
---
status: active          # active | on_hold | blocked | completed | archived
priority: P2            # P1 | P2 | P3 | P4
key: ACME-PROJ-01       # optional short key for cross-references (e.g. Jira project key prefix)
owner: "@ADMIN"         # @nick from People.md (note: @ values must be quoted in YAML)
started: 2026-04-01
target: 2026-Q3         # ISO date or quarter string
jira_project: ACME      # optional: links to Jira project for sync
---
```

**PyYAML note:** YAML reserves `@` as a reserved indicator. The materialiser auto-quotes `owner: @ADMIN` → `owner: "@ADMIN"` before parsing. Do not rely on unquoted `@` in frontmatter persisting through any YAML round-trip.

### Full Project File Template

```markdown
---
status: active
priority: P2
owner: "@ADMIN"
started: 2026-04-01
target: 2026-Q3
jira_project: ACME
---

# <Project Name>

One-line description of what this project is.

## Why

One paragraph describing what this project is for and why it exists. This is the
"why it matters" context the AI uses when generating task summaries.

## Current State

What's the situation right now? Last meaningful update with date.

## Tasks

- [ ] Task description — @owner if delegated — due:YYYY-MM-DD if time-bound
- [x] Completed task — done:YYYY-MM-DD
- [-] Cancelled task — reason
- [>] In-progress task (set by plan pipe materialisation; do not set manually)

## Decisions

- YYYY-MM-DD: What was decided and why

## Open Questions

- Things that need answers before progress can continue

## Playbook

- [ ] @monthly Proxmox Updates {{mon-yyyy}}
- [ ] @monthly:day:20 GST Payment {{mon-yyyy}}

## Log

- YYYY-MM-DD: What was done — brief description of action taken or progress made

---

## AI Notes

(AI-generated observations; not edited by the user directly)
```

### Section Order Convention

Sections should appear in this order (the materialiser and AI both rely on it):
1. YAML frontmatter (`---` block)
2. H1 title
3. One-line description paragraph
4. `## Why`
5. `## Current State`
6. `## Tasks`
7. `## Decisions`
8. `## Open Questions`
9. `## Playbook` (optional)
10. `## Log` — always second-to-last or last
11. `---` separator then `## AI Notes` — always last

The materialiser inserts new sections above `## AI Notes` and `## Log` to preserve their trailing position.

### `Index.md` (Auto-generated)

Each `<OU>/Projects/Index.md` is rebuilt automatically by the materialiser on each daily run. It contains a summary table of all projects in the OU with key, name, filename, status, and a truncated description. **Do not edit Index.md manually** — changes will be overwritten on the next materialisation run.

---

## Daily Log Files (`<OU>/Daily/<YYYY-MM-DD>.md`)

One file per calendar day per OU. Created by the materialiser at midnight with content seeded from four sources (carry-forward, plan pipe, recurring tasks, daily checklist). The user then adds their own content throughout the day.

### YAML Frontmatter

```yaml
---
date: 2026-06-18 Wednesday
---
```

### Full Daily Log Template

```markdown
---
date: YYYY-MM-DD <DayOfWeek>
---

## Tasks

- [ ] ↳ Carry-forward task from yesterday (prefixed with ↳ by materialiser)
- [ ] PROJ-KEY: Plan pipe task (prefixed with project key if heading had key)
- [ ] Weekly recurring task due:Jun-18 ACME/Recur/weekly-standup.md
- [x] Completed task

## Daily checklist

- [ ] Check email
- [ ] Team standup

## Log

(user adds notes here throughout the day)
```

### Daily File Sources (Set by Materialiser)

The materialiser populates the daily file from four sources, in this order:

1. **Carry-forward** (`↳` prefix): unchecked tasks from the `## Tasks` section of the most recent previous daily file. Only carries from `## Tasks`, not from `## Daily checklist`. Re-running strips stacked `↳ ↳` prefixes.

2. **Plan pipe** (`start:` / `due:` matching): scans three plan files — `<OU>/Plans/<YYYY>.md`, `<OU>/Plans/<YYYY>-Q<n>.md`, `<OU>/Plans/<YYYY>-<MM>.md` — for `- [ ]` lines whose `start:<today>` or `due:<today>` token matches today (both ISO `YYYY-MM-DD` and short `Mon-DD` forms accepted). Each matched plan line is rewritten to `- [>]` in the plan file (scheduled marker). The bullet inserted in the daily file is prefixed with the project key if the task appeared under a heading with a key like `## Infrastructure Upgrade (INFRA-UP26)`.

3. **Weekly recur** (`Recur/` files with `cadence: weekly` that fire today): task inserted with source path appended.

4. **Daily checklist** (items from `<OU>/Recur/Daily.md`): appears under `## Daily checklist`.

All merges are idempotent — re-running the materialiser on a file that already exists merges only new items, never duplicates existing ones.

---

## Plan Files (`<OU>/Plans/`)

Plan files hold forward-planned tasks for a period. They are the intermediate layer between recurring templates and daily files.

### File Naming

| Cadence   | Filename pattern              | Example            |
|-----------|-------------------------------|--------------------|
| Annual    | `<YYYY>.md`                   | `2026.md`          |
| Quarterly | `<YYYY>-Q<1-4>.md`            | `2026-Q2.md`       |
| Monthly   | `<YYYY>-<MM>.md`              | `2026-06.md`       |

### Plan File Format

```markdown
# ACME — 2026-Q2

## Recurring

- [ ] Monthly review @ADMIN due:Jun-30 MEDIUM ^R:a1b2c3d4-Q2
- [>] Payroll processing @ADMIN due:Jun-20 ^R:e5f6g7h8-M06

## <Project Name> (PROJ-KEY)

- [ ] Task description due:2026-06-15
- [x] Completed plan task done:2026-06-01
- [>] In-progress — was [ ], set to [>] by plan pipe when it hit start: date
```

### Task State in Plan Files

- `- [ ]` — pending (not yet scheduled to Daily)
- `- [>]` — scheduled (plan pipe has moved this to a Daily file for today)
- `- [x]` — completed
- `- [-]` — cancelled

The materialiser only rewrites `[ ]` → `[>]`. It never rewrites `[x]` or `[-]`.

### `^R:` Idempotency Markers

When a recurring task template (`<OU>/Recur/<name>.md`) is manifested into a plan file, the materialiser appends a `^R:<hash>-<period>` marker:

- `<hash>` — first 8 hex chars of SHA-1 of the Recur file's stem (filename without extension). Stable unless the file is renamed.
- `<period>` — period suffix: `M<MM>` for monthly, `Q<n>` for quarterly, `Y` for yearly.

Example: `^R:a1b2c3d4-M06`

**Do not remove `^R:` markers** — they are the idempotency mechanism. If a marker is removed, the task will be re-materialised into the plan file on the next daily run.

To find which Recur file produced a given marker: iterate `<OU>/Recur/*.md` and compare `sha1(stem)[:8]` until the hash matches.

---

## Recurring Task Files (`<OU>/Recur/`)

Each recurring task template lives as its own `.md` file with YAML frontmatter controlling when and how it materialises.

### `Recur/Daily.md` (Special File)

This file does NOT use YAML frontmatter. It is a simple checklist of items to insert under `## Daily checklist` in every daily log. Format:

```markdown
# Daily Checklist

- [ ] Check email
- [ ] Team standup
- [ ] Review open tasks
```

Both `## Heading` style and `- [ ] item` style are parsed. Headings (`## `) become checklist items with the heading text. Plain list items have their prefix stripped. All items are inserted as `- [ ] <item>` in the daily file.

### Recur File Format (YAML Frontmatter)

Each non-Daily recur file uses YAML frontmatter to declare its schedule:

```yaml
---
title: Monthly Project Review
cadence: monthly           # daily | weekly | monthly | quarterly | yearly
schedule: day:last         # schedule within the period (see below)
owners:
  - ADMIN                    # list of usernames (without @; @ in task lines only)
priority: medium           # high | medium | low (default: medium)
---

Optional body text — used as task notes or context. Not currently rendered.
```

### Cadence and Schedule Grammar

#### `daily`

No `schedule` field needed. Fires every day. Items appear in `## Daily checklist` via `Recur/Daily.md` (the special file); non-Daily.md daily files appear in `## Tasks`.

#### `weekly`

```yaml
cadence: weekly
schedule: weekday:mon         # fires on Mondays
schedule: weekday:mon,thu     # fires on Mondays and Thursdays
```

Default (no schedule): fires on Monday.

#### `monthly`

```yaml
cadence: monthly
schedule: day:1              # 1st of month (default)
schedule: day:15             # 15th of month
schedule: day:last           # last day of month
schedule: day:last-week      # first day of the last 7-day window of the month
```

Day values are clamped to the actual last day of short months (e.g. `day:31` in February → Feb 28 or 29).

#### `quarterly`

```yaml
cadence: quarterly
schedule: m1-15              # 15th day of the first month of the quarter
schedule: m2-last            # last day of the second month of the quarter
schedule: m3-last-week       # first day of last 7-day window of month 3 of quarter
schedule: week-of-q:1        # first Monday of the quarter
schedule: week-of-q:last     # first Monday of the last week of the quarter
```

Default (no schedule): 1st of m1 of the quarter.

#### `yearly`

```yaml
cadence: yearly
schedule: day:MM-DD          # recurring: e.g. day:04-15 fires Apr 15 every year
schedule: day:YYYY-MM-DD     # one-shot: e.g. day:2026-06-01 fires once
```

Default (no schedule): January 1.

### Materialisation Routing

| Cadence   | Route                                     | Target                          |
|-----------|-------------------------------------------|---------------------------------|
| `daily`   | `Daily.md` → `## Daily checklist`         | `<OU>/Daily/<date>.md`          |
| `weekly`  | Direct → `## Tasks` (user-owned)          | `<OU>/Daily/<date>.md`          |
| `weekly`  | Direct → Govern (team-owned)              | `<OU>/Govern/<YYYY-MM>.md`      |
| `monthly` | Recur → Plan → Daily (via plan pipe)      | `<OU>/Plans/<YYYY>-<MM>.md`     |
| `quarterly` | Recur → Plan → Daily (via plan pipe)    | `<OU>/Plans/<YYYY>-Q<n>.md`     |
| `yearly`  | Recur → Plan → Daily (via plan pipe)      | `<OU>/Plans/<YYYY>.md`          |

"User-owned" means the `owners` list includes the current user's username. "Team-owned" means the `owners` list does NOT include the current user — these go to the Govern file instead so the user can track delegated work without cluttering their own Daily.

### Owner Filtering

If a recur file has no `owners` field at all, it is treated as owned by all users (backwards-compatible with templates created before the owners field was added). If `owners` is present but the current user is not in it, the task goes to `Govern` instead of the user's `Daily`.

---

## Governance Files (`<OU>/Govern/<YYYY-MM>.md`)

Governance files track team-delegated recurring tasks — tasks owned by team members (not the user) that the user needs to oversee. Created and updated by the materialiser automatically.

### Format

```markdown
# ACME — Govern — June 2026

## Carry-overs

- [ ] Pending item from May *(overdue from 2026-05)*

## Monthly

- [ ] Monthly Payroll Processing @Accountant due:Jun-30
- [ ] Monthly IT Maintenance @ITTeam due:Jun-20

## Weekly

- [ ] Weekly Report @TeamLead due:Jun-18 2026-W25

## Quarterly

- [ ] Q2 Compliance Check @ComplianceOfficer — Jun
```

### Carry-overs

On the 1st of each month, the materialiser checks the previous month's Govern file for unchecked tasks (`- [ ]` lines) and carries them forward to the new month's file under `## Carry-overs` with an `*(overdue from <YYYY-MM>)*` annotation.

### Section Headings in Govern

Sections are organised by cadence (`## Monthly`, `## Weekly`, `## Quarterly`, `## Yearly`). Tasks are inserted under the correct section heading.

---

## People.md

A single file at the corpus root (`md/People.md`) containing a profile section for every person the user interacts with. The AI resolves `@nick` mentions in task lines by looking up the nick in People.md.

### Format

```markdown
# People

## Full Name

- **Nick**: ADMIN
- **Role**: Director of Operations
- **OU**: ACME
- **Relationship**: Self | Reportee | Peer | Vendor | Client
- **Contact**: admin@company.com / +1-555-0100 / @TelegramHandle
- **Responsibilities**: What they own and are accountable for.
- **Traits**: How to work with them — communication style, strengths, quirks,
  preferred tone for messages drafted by the AI.
- **Notes**: Free-form context, history, anything that helps the AI.

## Another Person

...
```

**Nick resolution:** When the AI encounters `@ADMIN` in a task line, it searches `People.md` for `- **Nick**: ADMIN` (case-insensitive) in any H2 section, then uses the full `## Full Name` heading as the person's identity.

---

## Inbox.md

A single file at the corpus root (`md/Inbox.md`) for unprocessed quick-captures. Format is freeform but typically a timestamped bullet list:

```markdown
# Inbox

- 2026-06-18T09:15: Need to follow up with vendor on pricing
- 2026-06-18T14:30: Consider upgrading Proxmox hosts before year-end
```

Items are appended here when:
- The user uses quick-capture via UI or chat and the destination project is not clear from context.
- The AI captures an action item from an email or chat but cannot confidently route it to a project.

The user is expected to periodically triage Inbox.md and move items to the appropriate project files.

---

## Task Syntax Reference

Tasks are GitHub-style checklist items. The full syntax for a task line:

```
- [<status>] [↳ ] [<KEY>: ] <description> [@<owner>] [due:<date>] [done:<date>] [<PRIORITY>] [<source-ref>] [<idempotency-marker>]
```

### Status Characters

| Syntax  | Meaning                                                                  |
|---------|--------------------------------------------------------------------------|
| `- [ ]` | Pending — open task                                                      |
| `- [x]` | Done — completed task                                                    |
| `- [X]` | Done — accepted alternative capitalisation                               |
| `- [>]` | Scheduled — set by plan pipe when a plan task is moved to a daily file   |
| `- [-]` | Cancelled — task will not be done                                        |

**Do not set `[>]` manually.** It is set by the materialiser's plan pipe and cleared (to `[x]`) when the user completes the task.

### Inline Annotations

| Annotation          | Format                                   | Example                         |
|---------------------|------------------------------------------|---------------------------------|
| Carry-forward       | `↳ ` prefix (Unicode U+21B3)             | `- [ ] ↳ Review invoice`        |
| Project key prefix  | `<KEY>: ` at start of description        | `- [ ] INFRA-UP26: Update docs` |
| Due date            | `due:<date>`                             | `due:2026-06-30` or `due:Jun-30`|
| Done date           | `done:<date>`                            | `done:2026-06-15`               |
| Owner               | `@<nick>`                                | `@ADMIN` or `@ITTeam`           |
| Priority (inline)   | `P1` / `P2` / `P3` / `P4`               | `P1` at end of line             |
| Priority (words)    | `HIGH` / `MEDIUM` / `LOW`               | `HIGH` at end of line           |
| Recur source ref    | posix path relative to md_root           | `ACME/Recur/weekly-review.md`   |
| Recur idempotency   | `^R:<hash8>-<period>`                    | `^R:a1b2c3d4-M06`               |
| Playbook idempotency| `^P:<slug>-<period-key>`                 | `^P:gst-payment-2026-06`        |

### Date Formats

Both ISO format and abbreviated format are accepted in `start:` / `due:` tokens:

- ISO: `due:2026-06-18`
- Short: `due:Jun-18` (month abbreviation + zero-padded day)

The materialiser checks both forms when scanning plan files for the plan pipe.

---

## Priority Conventions

| Tag      | Numeric | Meaning                       | Target timeframe     |
|----------|---------|-------------------------------|----------------------|
| `HIGH`   | `P1`    | Critical, must do             | Today or tomorrow    |
| `MEDIUM` | `P2`    | Important, should do          | This week            |
| `LOW`    | `P3`    | Nice to have                  | Next 30 days         |
| —        | `P4`    | Someday / backlog             | No specific deadline |

Priority can appear:
- In project frontmatter (`priority: P2`) as the project's overall priority.
- Inline in task lines to override the project default for that specific task.
- `MEDIUM` is the default and is typically omitted from task lines to reduce noise; only `HIGH`/`P1` and `LOW`/`P3`/`P4` are written when they differ from `MEDIUM`.

---

## OU (Organizational Unit) Conventions

- Each OU maps to exactly one top-level folder under `md/` and one subfolder under `Projects/` and `Plans/`.
- OU name is a simple string. Recommended: no spaces, use CamelCase or hyphens (e.g. `ACME`, `INFRA`, `HRAdmin`, `Personal`).
- Users typically have 3–8 OUs representing major project areas, teams, or life domains.
- The `ou` field is extracted from the folder path when building ChromaDB metadata for each indexed chunk.
- There is no nested domain layer inside an OU. If a large OU needs sub-categorisation, express it via filename prefix (`sales-campaign-q3.md`) or frontmatter tags, not subfolders. The materialiser expects a flat `Projects/` directory inside each OU.

---

## Git Commit Conventions in the MD Corpus

The MD repo uses a structured commit message format. Every commit is authored with one of three identities:

| Commit author identity           | When used                                          |
|----------------------------------|----------------------------------------------------|
| `PMA Bot <assistant@company.com>`   | AI-originated edits (chat UI SEARCH/REPLACE flow)  |
| `PMA Bot <mcp@company.com>`         | MCP server edits (Claude Desktop, external LLMs)   |
| `<username> <<username>@pma.local>` | User-authored saves (batch commits)                |

### Commit Message Prefixes

| Prefix       | When                                                                         |
|--------------|------------------------------------------------------------------------------|
| `AI: <summary>` | Chat-UI edits via `pma-edit` blocks; summary taken from first non-fence line of reply |
| `MCP: <summary>` | Edits from MCP server (`write_file` with `commit_prefix="MCP"`)         |
| `batch: <ISO timestamp>` | Periodic batch commit of all uncommitted user-authored changes  |
| `playbook: instantiated N item(s)` | Worker playbook materialisation                       |
| `<prefix>: <filename>` | Immediate commit with arbitrary prefix (e.g. `materialiser: daily file`) |

### Batch Commits

User edits via the in-app file editor are written to disk immediately but not committed. The worker runs `commit_pending()` periodically (configurable; default batch on significant changes). All uncommitted changes are staged and committed together as `batch: <YYYY-MM-DD HH:MM>`. This keeps git history clean without committing on every keystroke.

### Immediate Commits

AI `pma-edit` blocks always produce an immediate commit. MCP `write_file` calls with a `commit_prefix` also commit immediately. This ensures AI edits are always in git history even if the worker batch commit hasn't run yet.

---

## The `pma-edit` Block Format (AI Editing Protocol)

The AI uses a specific SEARCH/REPLACE format for all MD edits. This avoids the line-count arithmetic errors that plague standard unified diffs (`@@ -a,b +c,d @@`).

### Block Format

````
```pma-edit
file: <path relative to md_root>
<<<<<<< SEARCH
<exact existing content — whitespace, punctuation, everything must match>
=======
<replacement content>
>>>>>>> REPLACE
```
````

### Semantics

| SEARCH content | REPLACE content | Operation                                       |
|----------------|-----------------|-------------------------------------------------|
| Non-empty      | Non-empty       | Replace matched text in existing file           |
| Empty          | Non-empty       | Create new file (fails if file already exists)  |
| Non-empty      | Empty           | Delete matched block from file (leaves nothing) |

### Matching Rules

- **Path**: `file:` is relative to `md_root` (the user's `md/` directory). Absolute paths and `..` path components are rejected as unsafe.
- **Exact match required**: SEARCH must match byte-for-byte (after CRLF → LF normalisation). Whitespace, indentation, and punctuation must be identical.
- **Exactly one match**: If the SEARCH text appears 0 times or more than once in the file, the entire operation is rolled back and an error is returned. Widen the SEARCH context to make it unique.
- **CRLF normalisation**: Both the file on disk and the SEARCH/REPLACE content are normalised to LF before matching. The result is always written as LF.

### Multi-block Replies

A single AI reply can contain multiple `pma-edit` blocks. All blocks in one reply are applied atomically: if any block fails (no match, multiple matches, unsafe path, file not found), all blocks that already landed on disk are rolled back via `git checkout HEAD -- <touched-files>`, and newly-created files are removed via `git clean -fd`.

All successful blocks from one reply are committed together in a single `AI: <summary>` commit. The commit summary is the first non-empty, non-fenced line of the reply (truncated to 72 chars).

### Creating New Files

To create a new file, use an empty SEARCH block. If the file already exists, the operation fails with a descriptive error directing the AI to use a SEARCH anchor instead (to avoid clobbering materialiser-written content like `## Recurring` sections).

---

## Project Playbook Format

A playbook is a list of recurring task templates embedded directly in a project file's `## Playbook` section. Unlike `Recur/` files (which route tasks to Daily or Govern), playbook instances appear only in the project's own `## Tasks` section — they do not bubble to the Daily inbox.

### Use Cases

- Monthly maintenance backlogs where execution state belongs with the project (e.g. IT maintenance queue).
- Multi-step yearly playbooks (e.g. annual audit phases with different due dates per phase).
- Any recurring work that is project-specific and should not spam the user's Daily.

### Line Grammar

```
- [ ] @<cadence>[:<schedule>] [@<param>=<value>]* <title with {{tokens}}>
```

Lines in `## Playbook` that do not match this grammar are silently ignored.

### Cadence and Schedule (Playbook)

Same schedule grammar as `Recur/` frontmatter but expressed inline in the task line:

| Cadence     | Example                              | Fires                                        |
|-------------|--------------------------------------|----------------------------------------------|
| `@monthly`  | `@monthly`                           | 1st of each month                            |
| `@monthly`  | `@monthly:day:20`                    | 20th of each month                           |
| `@monthly`  | `@monthly:last`                      | Last day of month                            |
| `@quarterly`| `@quarterly:m3-last`                 | Last day of month 3 in each quarter          |
| `@yearly`   | `@yearly:day:04-01`                  | April 1 each year                            |

### Title Tokens

Tokens in the title are substituted at materialise time:

| Token          | Example (today = 2026-04-15)  |
|----------------|-------------------------------|
| `{{yyyy}}`     | `2026`                        |
| `{{mon}}`      | `Apr`                         |
| `{{mon-yyyy}}` | `Apr-2026`                    |
| `{{yyyy-mm}}`  | `2026-04`                     |
| `{{quarter}}`  | `Q2`                          |
| `{{quarter-yyyy}}` | `Q2-2026`                 |

Unknown tokens are left as the literal `{{token}}` in the rendered task so the typo is visible.

### Parameters

| Param        | Effect                                                            |
|--------------|-------------------------------------------------------------------|
| `@due=<schedule>` | Adds `due:YYYY-MM-DD` to the rendered task                  |
| `@slug=<id>` | Override the auto-derived slug used in the `^P:` anchor. Use when titles change between cycles. |

### `^P:` Anchor (Idempotency)

Each materialised playbook instance ends with `^P:<slug>-<period-key>`:

- `slug`: derived from the title with `{{tokens}}` stripped, lowercased, punctuation removed, spaces and hyphens collapsed. Example: `Proxmox Updates {{mon-yyyy}}` → `proxmox-updates`.
- `period-key`: `YYYY-MM` for monthly, `YYYY-Qn` for quarterly, `YYYY` for yearly.

If `## Tasks` already contains a line with the anchor, no new instance is appended. The anchor is visible in raw MD; the frontend hides it from the rendered task display.

### Full Playbook Example

```markdown
## Playbook

- [ ] @monthly Proxmox Updates {{mon-yyyy}}
- [ ] @monthly All Frappe Apps Update {{mon-yyyy}}
- [ ] @monthly:day:20 GST Payment {{mon-yyyy}}
- [ ] @monthly:last Month-end backup verification {{mon-yyyy}}
- [ ] @yearly:day:04-01 @due=day:04-30 Audit Phase 1 — books closing {{yyyy}}
- [ ] @yearly:day:05-01 @due=day:05-31 Audit Phase 2 — schedules + lead-sheets {{yyyy}}
- [ ] @quarterly:m3-last @slug=q-closeout Quarter close-out {{quarter-yyyy}}
```

---

## Archive Convention

Files can be archived by either:

1. **In-place status change**: Set `status: archived` in the YAML frontmatter. The file stays in its current location.
2. **Physical move**: Move the file to an `archive/` subfolder that mirrors the OU structure (e.g. `<OU>/Projects/archive/<project>.md`).

In both cases, the `archived` metadata field in ChromaDB is set to `"true"` (stored as a string due to ChromaDB limitations). RAG queries exclude archived files by default. Pass `include_archive=True` to the indexing service query to include them.

Files with `status: completed` are NOT automatically archived — only `status: archived` triggers the archived metadata flag.

---

## ChromaDB Index Metadata Fields

Each chunk in the vector index carries the following metadata:

```python
{
    "ou": "ACME",              # organizational unit (top-level folder name)
    "path": "ACME/Projects/infrastructure-upgrade.md",  # posix path relative to md_root
    "mtime": 1718700000.0,     # file modification time (Unix timestamp float)
    "archived": "false",       # "true" or "false" as string (ChromaDB limitation)
}
```

**Note on types:** ChromaDB metadata values must be strings, integers, or floats — not booleans. The `archived` field is stored as the string `"true"` or `"false"` and must be compared as a string in filter queries.

The embed model is `BAAI/bge-small-en-v1.5` running locally via fastembed (ONNX, no API call needed, ~100 MB model cache). ChromaDB persists to `DATA_ROOT/<username>/db/chroma/`.

The index is updated by:
1. **Periodic sync**: Worker runs `IndexingService.sync_index()` every `INDEX_SYNC_INTERVAL_SEC` seconds (default 300). Detects changed and deleted files by comparing `mtime` and file listing against the index.
2. **Full rebuild**: Triggered manually via UI or CLI. Wipes the collection and re-indexes all files.
3. **Single-file refresh**: `IndexingService.refresh_single_file(rel_path)` for fast updates after a known single-file edit.

The `db/` directory (SQLite + ChromaDB) is derived state — fully rebuildable from the MD corpus and Jira snapshot. It can be safely deleted to reset the app to a clean state.

---

## News Watch Integration

News watch runs nightly per OU / project and uses the Anthropic Message Batches API (async batch processing) to search for relevant news about active projects.

- **Trigger**: Nightly cron job at midnight (configurable) or on-demand via Settings UI.
- **Processing**: Each active project in each OU is submitted as a batch item. Batch processing is async — results are polled by a separate 5-minute job.
- **Results**: News summaries are written to the project file under a `## News` section using the `write_file` path (pending batch commit).
- **Day-of-week filter**: By default, news watch only runs on weekdays. Set `PMA_NEWS_RUN_ALL=1` to bypass.
- **Feedback**: User can mark news items 👍/👎. Feedback stored as metadata; used to tune future relevance.

The nightly cron is disabled in development by setting `PMA_NEWS_WATCH_CRON_DISABLED=1`. Manual triggers from the Settings UI still work even when the cron is disabled, and the 5-minute finalize poll job still runs to process manually-submitted batches.

---

## Writing Style the AI Enforces

When the AI creates or edits MD corpus files, it follows these conventions:

- **Professional, warm, direct.** Action-oriented and concise; no filler or padding.
- **Clean Markdown formatting** — headings, checklists, tables where appropriate.
- **Always include deadlines and responsible person** when they are known.
- **Proactive** — suggests next actions, flags risks, surfaces stuck work.
- **Does not invent** projects, tasks, or people. Only works with what's in the files or what the user explicitly states.
- **No silent edits** — every AI edit goes through the `pma-edit` SEARCH/REPLACE flow which produces a git commit.
- **Does not send** external messages (email, Telegram) without explicit user confirmation. Draft first, send on command.
- **Does not echo** the user's message back. Acknowledge and act.

---

## Template Files (App-Shipped, Not Per-User)

Templates live in the app code repo at `code/src/templates/user/` and are shipped with the app. They are NOT part of the user's MD corpus and are never indexed by ChromaDB. The AI reads them via the `read_src` tool.

On first onboarding, the template seed root is copied to the user's `data/<username>/` directory.

Template path in app repo:
- `code/src/templates/user/ABOUT.md` — user profile template
- `code/src/templates/user/md/` — MD corpus templates (project, daily, weekly, people entry)
- `code/src/templates/user/ExampleOU/` — example OU folder structure shipped as a starter corpus

The AI uses `read_src("templates/<path>")` to fetch a template skeleton before creating a new file from it.
