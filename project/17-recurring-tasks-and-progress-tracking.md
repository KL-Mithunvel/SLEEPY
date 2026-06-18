# Recurring Tasks & Progress Tracking System

## The Planning Hierarchy

PMA implements a complete top-down planning hierarchy: recurring templates → plan files → daily logs. Tasks flow down through the hierarchy automatically via the materialiser (runs nightly at midnight). The user's job is to maintain the recurring task templates; the system handles all the mechanical production of plan files and daily logs.

```
Recur/<name>.md          ← Templates: WHAT repeats and WHEN
      ↓ materialise_non_daily (monthly/quarterly/yearly)
<OU>/Plans/<period>.md   ← Planned work for the period with due dates
      ↓ apply_plan_pipe (start:/due: matching today)
<OU>/Daily/<date>.md     ← TODAY's tasks pulled from plans
      ↓ carry-forward (unchecked tasks from yesterday)
<OU>/Daily/<date+1>.md   ← Tomorrow inherits what wasn't done
```

Additionally:
- `Recur/Daily.md` → daily checklist items (bypasses Plans, goes directly to Daily)
- `Recur/<weekly>.md` → weekly tasks (bypasses Plans, goes directly to Daily on matching weekday)
- Team-owned recurs → `Govern/<YYYY-MM>.md` instead of user's Daily

## Recur Files: The Source of Truth for Repetition

### Location
```
<md_root>/<OU>/Recur/
  Daily.md           # daily checklist (special file — no frontmatter, flat list)
  <task-name>.md     # one file per recurring task template
```

### `Recur/Daily.md` — Daily Checklist
This is a special flat file with no YAML frontmatter. Every line is a checklist item that gets copied verbatim to every daily file's `## Daily checklist` section.

```markdown
Morning review: read yesterday's log
Check email inbox
Review Jira board
Check monitoring dashboards
Review news in inbox.md
```

These appear in every daily file regardless of day of week. Use for truly daily habits.

### Recurring Task Template File (`Recur/<name>.md`)
Every other recur file uses YAML frontmatter to declare its schedule:

```yaml
---
title: Monthly Infrastructure Review
cadence: monthly
schedule: day:last-week
owners:
  - ADMIN
  - JD
priority: P2
---

## Description
Full infrastructure review: capacity, costs, incidents, roadmap updates.
Agenda: ...

## Checklist
- [ ] Review cloud cost dashboard
- [ ] Check capacity headroom (>30% free)
- [ ] Review incident count vs. last month
- [ ] Update roadmap in Confluence
```

The body (below frontmatter) is NOT used by the materialiser — it stays in the recur file as reference documentation. The materialiser only uses the frontmatter to generate the task bullet.

## Frontmatter Reference for Recur Files

### `cadence:` (Required)
| Value | Where tasks land | Via |
|-------|-----------------|-----|
| `daily` | `Daily/<date>.md` → `## Daily checklist` | Direct (not via Plans) |
| `weekly` | `Daily/<date>.md` → `## Tasks` | Direct (not via Plans) |
| `monthly` | `Plans/<YYYY-MM>.md` → `## Recurring` | materialise_non_daily |
| `quarterly` | `Plans/<YYYY>-Q<n>.md` → `## Recurring` | materialise_non_daily |
| `yearly` | `Plans/<YYYY>.md` → `## Recurring` | materialise_non_daily |

### `schedule:` (Required for all except daily)

#### Weekly Cadence Schedules
```yaml
schedule: weekday:mon              # every Monday
schedule: weekday:mon,thu          # every Monday and Thursday
schedule: weekday:fri              # every Friday
```
Default (if schedule absent): Monday

#### Monthly Cadence Schedules
```yaml
schedule: day:1                    # 1st of every month
schedule: day:15                   # 15th of every month
schedule: day:last                 # last day of month (auto-adjusts for 28/30/31)
schedule: day:last-week            # first day of the last 7 days of the month
```
Default: day:1

#### Quarterly Cadence Schedules
```yaml
schedule: m1-15                    # 15th of 1st month of quarter (Jan/Apr/Jul/Oct)
schedule: m2-01                    # 1st of 2nd month of quarter (Feb/May/Aug/Nov)
schedule: m3-last                  # last day of 3rd month of quarter (Mar/Jun/Sep/Dec)
schedule: m3-last-week             # last week of final quarter month
schedule: week-of-q:1              # 1st week of quarter (Monday)
schedule: week-of-q:last           # last week of quarter (Monday)
```
Default: m1-1 (1st of first quarter month)

#### Yearly Cadence Schedules
```yaml
schedule: day:04-15                # April 15th every year (recurring)
schedule: day:12-last              # December last day every year
schedule: day:2026-06-30           # June 30, 2026 ONLY (one-shot)
```
Default: Jan 1st

### `owners:` (Optional)
Controls whether the task goes to user's Daily/Plans or to `Govern/`:

```yaml
# No owners field → all users get it (backwards-compatible default)
# ---

# Specific owners list → only these users get it in their Daily/Plans
owners:
  - ADMIN              # nick, not @nick (no @ prefix)
  - JD

# Task goes to Govern if current user is NOT in owners list
# (so team members who need to oversee delegated work see it in Govern)
```

**Owner routing logic:**
- If `owners:` absent → user-owned (goes to Daily/Plans)
- If `owners:` present AND current user's nick is in list → user-owned (goes to Daily/Plans)
- If `owners:` present AND current user's nick is NOT in list → team-owned (goes to Govern)

### `priority:` (Optional)
```yaml
priority: P1    # P1 | P2 | P3
```
Appended to the materialised task bullet (except P2/medium which is default and omitted).

### `title:` (Optional but recommended)
```yaml
title: Monthly Infrastructure Review
```
Used as the task text in the materialised bullet. If absent: the filename stem is used.

## How the Materialiser Works (Nightly Pipeline)

The materialiser runs at **00:00 every night** via `materialise_job` in worker.py. It is entirely deterministic — no LLM calls. Safe to re-run (idempotent at every layer).

### Stage 1: `materialise_non_daily` — Recur → Plans

Processes monthly/quarterly/yearly recur files only. Weekly and daily skip this stage.

For each recur file in `<OU>/Recur/`:
1. Parses YAML frontmatter
2. Checks cadence: if not monthly/quarterly/yearly → skip
3. Checks ownership: if owner-filtered and current user not in owners → skip (handled by Stage 3 / Govern)
4. Checks if already materialised: looks for `^R:<hash>-<period>` marker in target plan file → skip if found
5. Computes due date from schedule spec for current period
6. Builds the task bullet
7. Appends to `<OU>/Plans/<period>.md` under `## Recurring` section

**Idempotency marker**: `^R:<sha1[:8]>-<period>` appended to each bullet:
```
- [ ] Monthly Infrastructure Review @ADMIN due:Jun-30 ACME/Recur/infra-review.md ^R:a3f8b2c1-M06
```
- `a3f8b2c1` = first 8 hex chars of SHA-1 of the recur file's stem ("infra-review")
- `M06` = period suffix (M=monthly, month 06; Q2 = quarterly Q2; Y = yearly)

The marker means: "this task was already materialised for period M06; don't re-insert it."

**Do not remove `^R:` markers** — they are the idempotency mechanism. If removed, the task will be re-materialised.

### Stage 2: `materialise_daily` — Seeds Daily File

Seeds `<OU>/Daily/<today>.md` from four sources in order:

#### Source 1: Carry-Forward (`↳` prefix)
Reads the most recent daily file before today (by filename date). Extracts all `- [ ]` lines from `## Tasks` section only (NOT from `## Daily checklist`). Prefixes each with `↳`:

```
- [ ] ↳ Deploy staging environment due:Jun-30    ← carried from yesterday
```

Rules:
- Only reads `## Tasks` section (stops at next `## ` heading)
- Only carries `- [ ]` lines (not `[x]`, `[>]`, `[-]`)
- Strips existing `↳` prefix before re-adding (prevents `↳ ↳ ↳` stacking)
- If yesterday's file has no unchecked tasks: no carry-forward (nothing to carry)

#### Source 2: Plan Pipe (`apply_plan_pipe`)
Scans year/quarter/month plan files for tasks with `start:<today>` or `due:<today>` (either ISO `YYYY-MM-DD` or short `Mmm-DD` form):

```python
# Today: 2026-06-30
start_tokens = ("start:2026-06-30", "start:Jun-30")
due_tokens   = ("due:2026-06-30",   "due:Jun-30")
```

For each matching `- [ ]` line in any plan file:
1. Rewrites the plan file line: `- [ ]` → `- [>]` (marks as "scheduled")
2. Adds the line to today's daily tasks (prefixed with project key if in a keyed section)

```
# In Plans/2026-Q2.md before plan pipe:
- [ ] Quarterly security audit m3-last ACME/Recur/security-audit.md ^R:b4a1c2d3-Q2

# After plan pipe (plan file is rewritten):
- [>] Quarterly security audit m3-last ACME/Recur/security-audit.md ^R:b4a1c2d3-Q2

# Added to Daily/2026-06-30.md → ## Tasks:
- [ ] PROJ-IT: Quarterly security audit m3-last ACME/Recur/security-audit.md ^R:b4a1c2d3-Q2
```

The `[>]` marker in the plan file means: "this task has been scheduled into a daily file; don't schedule it again."

#### Source 3: Weekly Recurring Tasks
Reads all `<OU>/Recur/*.md` files (except Daily.md) with `cadence: weekly`. For each that matches today's weekday schedule:

```
- [ ] Weekly team standup @ADMIN @JD due:Jun-18 ACME/Recur/standup.md
```

Slug-based idempotency: `_recur_slug(p)` generates a stable slug from the path, checked against existing daily file content to prevent double-insertion.

#### Source 4: Daily Checklist
Reads `<OU>/Recur/Daily.md` (flat list, no frontmatter). Each line becomes:
```
- [ ] Morning review: read yesterday's log
- [ ] Check email inbox
```

Placed under `## Daily checklist` (separate from `## Tasks`).

#### Merge vs. Create
- **First run today** (file doesn't exist): creates fresh file with YAML frontmatter, `## Tasks`, `## Daily checklist`, `## Log`
- **Subsequent runs** (file already exists): merges only NEW items into existing sections (de-duped against existing lines)

**Daily file frontmatter:**
```yaml
---
date: 2026-06-18 Thursday
---
```

### Stage 3: `materialise_govern` — Team-Owned Recur → Govern

For recur files where current user is NOT in the `owners:` list (delegated tasks):

```
<OU>/Govern/<YYYY-MM>.md
```

Structure:
```markdown
# Govern — 2026-06

## @JD

- [ ] Monthly deployment review @JD due:Jun-30 ACME/Recur/deploy-review.md ^R:c5d2e3f4-M06
- [ ] Weekly release notes @JD due:Jun-18 ACME/Recur/release-notes.md

## @SJ

- [ ] Monthly marketing report @SJ due:Jun-last ACME/Recur/mkt-report.md ^R:d6e3f4g5-M06
```

**Carry-overs**: On the 1st of each month, unchecked tasks from the previous month's Govern file are carried forward:
```
- [ ] *(overdue from 2026-05)* Monthly deployment review @JD ...
```

## Plan Files: The Intermediate Layer

Plan files are the period-scoped task lists that bridge recurring templates and daily work.

### Plan File Locations
```
<md_root>/<OU>/Plans/
  2026.md           # yearly plan
  2026-Q2.md        # quarterly plan (Q1-Q4)
  2026-06.md        # monthly plan
```

### Plan File Structure
```markdown
---
date: 2026-Q2
ou: Infrastructure
---

# Infrastructure — Q2 2026

## Goals
- Complete domain migration pilot by June 30
- Launch monitoring overhaul MVP

## IT-DOM25: Domain Migration 2025

- [ ] Run pilot with 20 users start:2026-06-01 due:2026-06-30
- [>] Configure ADConnect sync start:2026-05-15    ← scheduled (in today's daily)
- [x] Get budget approval done:2026-05-10           ← completed

## Recurring

- [ ] Quarterly security audit due:2026-06-30 ACME/Recur/security-audit.md ^R:b4a1c2d3-Q2
- [>] Team OKR review due:2026-04-01 ACME/Recur/okr-review.md ^R:a1b2c3d4-Q2  ← already scheduled
```

### Adding Tasks to Plan Files

Users add tasks to plan files manually (or via the AI):

```markdown
- [ ] Deploy to production start:2026-07-15 due:2026-07-31
- [ ] Conduct training sessions due:2026-08-15
- [ ] Go-live due:2026-09-01 P1
```

The plan pipe reads these nightly and activates them on their `start:` or `due:` dates.

### Creating Plan Files

Plan files for future periods are created:
1. **Manually** — user creates `Plans/2026-Q3.md` and adds tasks
2. **By materialiser** — when a Recur file targets a period, materialise_non_daily creates the plan file if it doesn't exist (with `## Recurring` section)
3. **Via AI** — ask the AI to help set up a quarterly plan

## Daily Files: Where Work Actually Happens

### Daily File Structure
```markdown
---
date: 2026-06-18 Thursday
---

## Tasks

- [ ] ↳ Deploy staging environment due:Jun-30                    ← carried forward
- [ ] IT-DOM25: Configure ADConnect sync start:2026-06-18         ← from plan pipe
- [ ] Weekly standup @ADMIN due:Jun-18 ACME/Recur/standup.md       ← from weekly recur

## Daily checklist

- [ ] Morning review: read yesterday's log
- [ ] Check email inbox
- [ ] Review Jira board

## Log

### Morning
<user writes here during the day>

### Evening
<user writes review here>
```

### How to Use Daily Files

1. **Morning**: Review `## Tasks` and `## Daily checklist`. Prioritise. Add ad-hoc tasks directly.
2. **During the day**: Check off `[x]` completed tasks. Add notes under `## Log`.
3. **Evening**: Review unchecked tasks. Move them to future plan files if they need a different date. Remaining `- [ ]` tasks auto-carry to tomorrow.

### Carry-Forward: The Zero-Inbox Safety Net

Any `- [ ]` task in `## Tasks` that isn't checked off by midnight is automatically carried to the next day's `## Tasks` with a `↳` prefix. This ensures nothing falls through the cracks.

**But**: carry-forward can accumulate clutter. Best practice:
- If a task won't happen this week: add a `start:` or `due:` date and remove the carry-forward by adding to a plan file
- If a task is no longer relevant: mark `[-]` (cancelled) to stop it carrying forward
- Don't leave more than 5-6 unchecked tasks in daily files

## Progress Tracking Over Time

### Weekly Review Pattern
The AI has a `weekly-review` skill that guides you through:
1. What was done this week (from daily files)
2. What wasn't done (unchecked carry-forwards)
3. Next week planning (update plan files with `start:` dates for next week)

### Monthly Review Pattern
The AI's `monthly-planning` skill helps you:
1. Review completed vs. pending tasks in the month's plan file
2. Carry outstanding items to next month's plan
3. Set quarterly OKR progress

### Quarterly Review Pattern
Using `quarterly-planning` skill:
1. Review the quarter's plan file (`Plans/2026-Q2.md`)
2. Assess OKR progress
3. Create next quarter's plan file with updated goals

### Annual Planning
Using `project-setup` and `quarterly-planning` skills:
1. Create `Plans/2027.md` with yearly goals
2. Break goals into quarterly milestones
3. Set up Recur files for annual recurring work

## Idempotency Guarantees

The entire materialiser pipeline is idempotent — safe to run multiple times per day:

| Operation | Idempotency Mechanism |
|-----------|----------------------|
| Recur → Plans | `^R:<hash>-<period>` marker in plan file |
| Recur → Daily | Slug check: `_recur_slug` vs. existing file content |
| Plan pipe → Daily | `[>]` marker in plan file (rewritten from `[ ]`) |
| Carry-forward | De-duped against existing `## Tasks` lines |
| Daily checklist | De-duped against existing `## Daily checklist` lines |
| Govern | Marker-based (same as Plans) |

## Example: Full Lifecycle of a Task

### 1. Define the recurring task

Create `<md_root>/ACME/Recur/monthly-cost-review.md`:
```yaml
---
title: Monthly cloud cost review
cadence: monthly
schedule: day:last-week
owners:
  - ADMIN
priority: P2
---
```

### 2. Materialiser runs (1st of June)

Materialiser runs `materialise_non_daily`, sees this recur file:
- Cadence: monthly → target: `ACME/Plans/2026-06.md`
- Due date: day:last-week = June 24 (first day of last 7 of June)
- Generates idempotency marker: `^R:f2a9b8c7-M06`

Appends to `ACME/Plans/2026-06.md`:
```markdown
## Recurring

- [ ] Monthly cloud cost review @ADMIN due:2026-06-24 ACME/Recur/monthly-cost-review.md ^R:f2a9b8c7-M06
```

### 3. Plan pipe fires (June 24)

Materialiser runs `materialise_daily` → `_due_today_from_plans()` scans plan files:
- Found: `due:2026-06-24` matches today
- Rewrites plan file: `- [ ]` → `- [>]`
- Adds to today's daily file under `## Tasks`:
  ```
  - [ ] Monthly cloud cost review @ADMIN due:2026-06-24 ACME/Recur/monthly-cost-review.md ^R:f2a9b8c7-M06
  ```

### 4. User completes the task (June 24)

User opens Today view, sees the task, completes it:
```
- [x] Monthly cloud cost review @ADMIN due:2026-06-24 ACME/Recur/monthly-cost-review.md ^R:f2a9b8c7-M06
```

### 5. Next month (July 1)

Materialiser runs again. Checks `ACME/Plans/2026-07.md`:
- No `^R:f2a9b8c7-M07` marker → not yet materialised for July
- Due date for July: day:last-week = July 24
- Appends new bullet with `^R:f2a9b8c7-M07` to July plan file

The cycle repeats monthly. The user sees the task on the 24th of every month.

## The `move-line` Feature (Inbox → Plans)

The corpus API `POST /api/corpus/move-line` lets users move a task line from one file to another:

**Example**: User has a task in `inbox.md` that should be in the Q3 plan:
```
Source: inbox.md
Line: "- [ ] Evaluate new monitoring vendor"
Target: ACME/Plans/2026-Q3.md
Action: move
```

The line is removed from `inbox.md` and appended to `2026-Q3.md`. From there, the user can add `start:` or `due:` dates and it will flow into the daily file automatically.

Fuzzy matching (SequenceMatcher ≥ 0.6 on selections ≥ 12 chars) handles minor discrepancies in the line text when the user selects it in the UI.

## Govern: Team Task Tracking

The Govern view (`/team` in the frontend) shows the current month's `Govern/<YYYY-MM>.md` file, which tracks delegated tasks for team members.

Use it to:
- Confirm team members have completed their recurring responsibilities
- Follow up on overdue tasks (send email/Telegram directly from the Govern view — coming soon)
- See what each person in your team is working on this month

The `GET /api/corpus/govern?ou=&month=&include_done=` endpoint returns structured data:
```json
{
  "recur_tasks": [{"owner": "JD", "task": "...", "done": false}],
  "project_tasks": [...],
  "daily_tasks": [...],
  "people_nicks": ["ADMIN", "JD", "SJ"]
}
```
