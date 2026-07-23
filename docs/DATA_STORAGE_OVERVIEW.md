# How SLEEPY Stores Your Data (Plain-English Version)

> This is a simple-terms companion to `docs/CORPUS_SCHEMA.md` (the precise, code-verified reference). Read this one first if you just want the mental model; read that one when you need exact frontmatter fields or regex-level detail.

---

## The one-sentence version

**Your actual data is just text files in folders, tracked by Git — nothing else. Everything else in the system (databases, search index) is disposable machinery built on top of those text files, never the other way around.**

If every database in the system vanished tomorrow, your notes, tasks, and history would still exist untouched on disk (and in Git history). That's the whole design philosophy.

---

## Where things physically live

```
data/klm/                  ← this is "you" — one folder per user, in this case just you
├── ABOUT.md                 your profile / preferences
├── People.md                 contacts
├── inbox.md                  a junk drawer — quick capture, unsorted
├── NewsWatch.md              topics you want the news-watcher to track
├── Personal/                 one folder per "area of life"
├── VIT/                      (college)
├── SMTW/                      (your company / building SLEEPY itself)
└── db/                        SQLite + search index — internal plumbing, not your notes
```

`Personal/`, `VIT/`, `SMTW/` are **not** hardcoded categories — the app just looks at whatever folders exist. You could rename or add one tomorrow and nothing would break.

Inside each of those life-area folders:

| Folder / file | What it actually is, in plain terms |
|---|---|
| `<slug>.md` (e.g. `Personal/germany-masters.md`) | A project. One markdown file per project — goal, current state, tasks, decisions. This is the thing you actually write in. |
| `Recur/<name>.md` | A **template**, not a task itself. "Do Duolingo every day", "Pay rent monthly" — a recipe the system reads every night to generate real tasks. |
| `Daily/2026-07-23.md` | **Auto-generated every night.** Today's to-do list + a journal (`## Log`). You don't create these — a background job does, then you check things off during the day. |
| `Plans/2026-07.md` | Auto-generated monthly/quarterly/yearly rollup — "what recurring stuff is due this month." |
| `Govern/2026-07.md` | Auto-generated — tasks owned by someone *other* than you (shared/team items), grouped by person. No UI to view this yet. |
| `Archive/` | Where old daily files and finished projects get moved, so the active view doesn't fill up with clutter. |

---

## How a task actually gets in front of you (the nightly pipeline)

Every night at 00:05 IST, a robot (`materialiser.py`) rebuilds tomorrow's `Daily/<date>.md` from four ingredients:

1. **Carry-forward** — anything you didn't check off yesterday gets copied into today, marked with a little `↳` so you know it's a repeat, not new.
2. **Plan pipe** — if something in a `Plans/` file has a due date of today, it gets pulled into today's list.
3. **Daily/weekly habits** — every `Recur/` template marked `cadence: daily` gets a fresh checkbox today; `cadence: weekly` ones show up only on their scheduled weekday.
4. **The daily checklist file** (`Recur/Daily.md`) — a flat list of routine reminders, copied in every day verbatim.

The **Active Tasks** card on the Today view only ever reads *today's* `Daily/<date>.md` — it deliberately does not scan every project's task list, so your day doesn't get flooded by your entire backlog.

---

## What the two databases are actually for

- **SQLite** (`db/sqlite/pma.db`) — login sessions, the background job queue, and a log of every AI edit ever made. None of it is "your data" in the notes sense — it's operational bookkeeping. Wiping it loses history of *what the AI did*, not what you wrote.
- **ChromaDB** — a search index over your markdown, so the AI chat can find relevant context. Fully rebuildable from the markdown at any time (`tooling/run-md-index.bat`). If it's ever wrong or corrupted, you just reindex — no data loss.

Everything the AI writes goes through one narrow gate (`md_editor.py`): compute a diff → (optionally ask you to confirm) → write the file → commit to Git with a fixed author name. There's no code path where the AI (or a route) writes to your files directly without going through that.

---

## Where things can go stale (found while fixing today's bug)

While digging into the "finished tasks keep coming back" bug, a few structural soft spots turned up that are worth knowing about even though most don't need action *right now*:

1. **Text-matching is the whole identity system.** A task isn't a row with an ID — it's a literal string. "Is this the same task as yesterday?" is answered by comparing text. This is exactly what caused today's bug: once two lines had identical text (one checked, one not), the carry-forward logic couldn't tell they were "the same task" apart from a straight string comparison, and kept resurrecting the unchecked copy forever. Fixed now, but the underlying fragility (any code path that touches task lines has to reimplement its own "what counts as the same task" logic) remains.
2. **A recurring habit has no real "off switch."** `Recur/machine-vision-study.md` in your VIT folder has `status: inactive` sitting in its frontmatter (presumably from when you finished that exam), but nothing in the code actually reads that field — `cadence: daily` recur files generate a fresh task forever regardless of status. Right now the only way to actually stop one is to delete/rename the Recur file. Worth deciding if you want `status: inactive`/`paused` to be honored.
3. **Two different priority tag conventions exist in the same corpus.** Project files use `priority:high|medium|low` inline in a task line. The materialiser's own Recur→Plan bullets tag priority as a bare `P1`/`P3` word instead. Same concept, two incompatible spellings depending on which part of the pipeline wrote the line.
4. **`materialiser.py` — arguably the single most complex, most silently-bug-prone file in the codebase — has zero automated tests.** Every other backend module has a test file; this one doesn't. Today's bug (and the fix) went in without a single test proving it stays fixed.

### If I had to pick one thing to fix next

Give every task line a stable identity instead of relying on exact text (e.g. a short hidden id like `^T:ab12cd`, similar to the `^R:` markers Recur→Plan bullets already use for idempotency). That one change would make toggle/carry-forward/edit all correctness-by-construction instead of correctness-by-careful-string-matching, and it would have prevented this exact class of bug outright rather than needing a targeted patch. It's a bigger, deliberate change though — not something to do as a drive-by fix.

I can write tests for `materialiser.py` and/or look at the status/priority inconsistencies above if you want — just say which, since they're each their own small project rather than something to fold silently into the last fix.
