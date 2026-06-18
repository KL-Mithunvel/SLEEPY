# PMA Housekeeping System — Corpus Health Checks and Archiving

## Overview

`code/backend/housekeeping.py` implements two types of background maintenance:

1. **Checkers** (pure read) — scan the corpus for problems, emit `Finding` objects that are written as actionable items to `inbox.md ## Housekeeping`
2. **Actions** (mutating) — perform fixes directly (e.g. archive old daily files with a distinct git commit prefix)

`run_housekeeping()` is called by `worker.py::housekeeping_job` at **23:00 daily**. Each user's corpus is processed independently — errors on one user don't affect others.

---

## Finding Model

```python
@dataclass
class Finding:
    severity: Literal["info", "warn"]
    summary: str         # one-line user-actionable description
    location: str | None # "<OU>/Projects/Foo.md:23" or None for repo-level
```

Findings are written to `inbox.md` under `## Housekeeping` with exact-line dedup — a finding already present in the inbox is skipped. Each line uses the format:

```
- [ ] 🔎<location> — <summary>
```

For example:
```
- [ ] 🔎ACME/Projects/Infra-Upgrade.md — INFRA-UP26: all tasks done — mark `status: completed` or add next-step tasks
- [ ] 🔎ACME/Projects/Budget.md:14 — unknown owner @RAVI — add to People/ or fix typo
```

---

## Checker Registry

Checkers are registered via a decorator pattern:

```python
_CHECKERS: list[tuple[str, Callable[[CurrentUser], list[Finding]]]] = []

def register_checker(name: str):
    def decorator(fn):
        _CHECKERS.append((name, fn))
        return fn
    return decorator
```

`run_housekeeping()` iterates `_CHECKERS` and calls each in order. Exceptions in one checker are caught and logged; other checkers still run.

---

## Checkers

### 1. `project_status_hygiene`

**What it checks:** Active projects where all tasks are resolved (no pending `- [ ]` or scheduled `- [>]` tasks left).

**Why:** Projects that are functionally done but still marked `status: active` create noise. The checker nudges the user to mark them complete or add next steps.

**Logic:**
- Iterates all `<OU>/Projects/*.md` files (skipping `Index.md`)
- Parses YAML frontmatter; skips if `status != "active"`
- Skips projects whose key matches `/-QUEUE(?:-|$)/i` (perpetual backlogs — never "done")
- Counts lines matching `_TASK_LINE_RE = re.compile(r"^\s*- \[( |x|X|>)\] (.+)$")`
  - `[ ]` or `[>]` → unchecked
  - `[x]` or `[X]` → checked
- Emits a `warn` Finding when `checked > 0 AND unchecked == 0`

**Finding format:**
```
INFRA-UP26: all tasks done — mark `status: completed` or add next-step tasks
```

### 2. `missing_frontmatter`

**What it checks:** Required YAML frontmatter fields are missing from project or recur files.

**Required fields:**
| File type | Required fields |
|-----------|----------------|
| Projects (`<OU>/Projects/*.md`) | `key`, `status`, `owner` |
| Recur templates (`<OU>/Recur/*.md`) | `cadence`, `schedule` |

`<OU>/Recur/Daily.md` is skipped (flat list, intentionally has no frontmatter).
`<OU>/Projects/Index.md` is skipped (auto-generated).

**Finding format:**
```
missing `key:` in project frontmatter     → location: ACME/Projects/Budget.md
missing `cadence:` in recur frontmatter   → location: ACME/Recur/weekly-review.md
```

### 3. `unknown_owner`

**What it checks:** `@nick` mentions in task lines that don't resolve to a People file.

**Logic:**
- Builds a `valid` set per OU: all `.md` stems in `<OU>/People/` plus `nick:` values from their frontmatter, plus the current user's username (users don't have a self-profile by convention)
- Walks all `.md` files in the OU (excluding Archive/ and People/ directories)
- For each task line matching `_TASK_LINE_RE`, scans for `@([A-Za-z][\w-]*)` patterns
- **Skips lowercase mentions** — lowercase `@nick` patterns are treated as placeholder-style (e.g. `@person`, `@me`); real nicks are uppercase by convention
- Emits a `warn` Finding for any uppercase `@nick` not in the valid set

**Finding format:**
```
unknown owner @RAVI — add to People/ or fix typo    → location: ACME/Projects/Budget.md:14
```

### 4. `invalid_dates`

**What it checks:** `start:`, `finish:`, `due:` tokens on task lines that don't parse as a valid date format.

**Valid formats:**

| Token | Valid formats |
|-------|--------------|
| `due:` | `Mmm-DD` (e.g. `Jun-15`), `YYYY-MM-DD`, `Q1-Q4`, `YYYY-Qn`, `Mmm` (month name), `YYYY` |
| `start:` / `finish:` | `Mmm-YYYY` (e.g. `Jun-2026`), `YYYY-MM-DD`, `YYYY-MM`, `YYYY` |

**Logic:**
- Scans all `.md` files in each OU (excluding Archive/)
- Only checks task lines (matching `_TASK_LINE_RE`)
- Extracts `(start|finish|due):(\S+)` tokens; strips trailing `,.;:` from values
- Validates against the format tables above using regex + `date.fromisoformat()`

**Finding format:**
```
invalid due:'Jun-35'        → location: ACME/Projects/Budget.md:22
invalid start:'2026-13-01'  → location: ACME/Plans/2026-06.md:8
```

---

## Action: `archive_old_daily`

**What it does:** Moves `<OU>/Daily/<YYYY-MM-DD>.md` files older than `today - 365 days` into `<OU>/Archive/Daily/<YYYY>/<YYYY-MM-DD>.md`.

**Parameters:**
```python
def archive_old_daily(
    user: CurrentUser,
    today: date | None = None,
    *,
    days_threshold: int = 365
) -> dict:
```

**Process:**
1. Computes `cutoff = today - timedelta(days=365)`
2. Iterates all `<OU>/Daily/` directories
3. For each `YYYY-MM-DD.md` file where `file_date < cutoff`:
   - Builds destination path: `<OU>/Archive/Daily/<year>/<YYYY-MM-DD>.md`
   - Creates destination directory if needed
   - Performs filesystem rename (plain `src_path.rename(dst_path)`)
4. After all renames, stages all changes with `repo.git.add(A=True)` (stages renames as delete+add; git detects content similarity for history preservation)
5. Commits with prefix `archive:` as `ASSISTANT_AUTHOR`

**Return value:**
```python
{"moved": 5, "by_ou": {"ACME": 3, "INFRA": 2}}
```

**Error handling:**
- Individual rename failures are logged as warnings; other files still processed
- Commit failure is logged as warning (files are still moved on disk)
- If zero files need archiving, returns `{"moved": 0, "by_ou": {}}` without committing

**Note:** Git uses content similarity (`--follow`) to track renames through history. The plain `rename()` approach works because `git add -A` stages the pair as a delete+add, and git's similarity detection preserves the log.

---

## `write_findings_to_inbox`

```python
def write_findings_to_inbox(user: CurrentUser, findings: list[Finding]) -> int:
    """Returns count of newly added findings."""
```

**Process:**
1. Reads `<md_root>/inbox.md` (creates with `# Inbox\n\n` header if missing)
2. For each Finding, builds line: `- [ ] 🔎<location> — <summary>`
3. **Exact-line dedup:** skips any line already present in the inbox text
4. If `## Housekeeping` section exists: appends new lines under it
5. If `## Housekeeping` doesn't exist: creates it (inserted above `## AI Notes` and trailing sections by `_append_to_section`)
6. Writes via `md_patcher.write_file()` with prefix `housekeeping` and `ASSISTANT_AUTHOR`
7. Returns count of lines newly added

---

## `run_housekeeping` — Coordinator

```python
def run_housekeeping(user: CurrentUser, today: date | None = None) -> dict:
```

**Returns:**
```python
{
    "user": "username",
    "checkers": {
        "project_status_hygiene": 2,
        "missing_frontmatter": 1,
        "unknown_owner": 0,
        "invalid_dates": 3,
    },
    "findings_total": 6,
    "findings_added_to_inbox": 4,  # after exact-line dedup
    "archive": {
        "moved": 5,
        "by_ou": {"ACME": 3, "INFRA": 2}
    }
}
```

**Error handling:**
- Individual checker exceptions are caught and logged; `findings = []` for that checker
- `write_findings_to_inbox` exceptions are caught and logged; `added = 0`
- `archive_old_daily` exceptions are caught and logged; `archive = {"moved": 0, "by_ou": {}}`

---

## Worker Integration

```python
# worker.py
@scheduler.scheduled_job("cron", hour=23, minute=0, id="housekeeping_job")
def housekeeping_job():
    for user in get_all_users():
        result = run_housekeeping(user)
        log.info("housekeeping done for %s: %s", user.username, result)
```

Runs at 23:00 daily. Per-user processing is independent — one user's corpus errors don't block others.

---

## Helper Functions

### `_ous(user)` 
Yields `(ou_name, ou_dir)` for every OU under `user.md_root`. Skips hidden directories (`.git`, etc.) and directories named `archive`.

### `_md_files(ou_dir, subfolder)`
Yields `.md` files under `<ou>/<subfolder>/`. Skips Archive subtrees.

### Date Validation Functions

```python
def _is_valid_due(val: str) -> bool: ...     # validates due: token
def _is_valid_start_finish(val: str) -> bool: ...  # validates start:/finish: tokens
```

Accept multiple formats (see checker 4 above). Use regex matching + `date.fromisoformat()` for ISO dates.

---

## Regex Patterns Used

```python
_TASK_LINE_RE = re.compile(r"^\s*- \[( |x|X|>)\] (.+)$")   # matches task lines
_OWNER_RE = re.compile(r"@([A-Za-z][\w-]*)")                 # matches @mentions
_QUEUE_RE = re.compile(r"-QUEUE(?:-|$)", re.IGNORECASE)      # detects queue projects
```

---

## Extending Housekeeping

To add a new checker:

```python
@register_checker("my_new_checker")
def check_my_new_checker(user: CurrentUser) -> list[Finding]:
    out: list[Finding] = []
    for ou, ou_dir in _ous(user):
        # ... scan files ...
        out.append(Finding(
            severity="warn",
            summary="description of the problem",
            location="OU/Projects/File.md:42",  # or None for repo-level
        ))
    return out
```

Registering the function is enough — `run_housekeeping` picks it up automatically from `_CHECKERS`.

---

## Inbox Section Layout

After a housekeeping run, `inbox.md` has this structure:

```markdown
# Inbox

- 2026-06-18 `HIGH` `ACME` — Follow up on infrastructure maintenance update · ADMIN to action

## News

- [ ] 📰 [Title](URL) — source.com (2026-06-18) | *topic: ...*

## Housekeeping

- [ ] 🔎ACME/Projects/Budget.md — BUDGET-Q2: all tasks done — mark `status: completed` or add next-step tasks
- [ ] 🔎ACME/Projects/NewProject.md — missing `owner:` in project frontmatter
```

The `## Housekeeping` section accumulates over time. Users check off items when resolved. The exact-line dedup prevents the same finding from appearing twice on repeated runs.
