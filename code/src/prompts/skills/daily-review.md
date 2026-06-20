---
name: daily-review
description: Guide through morning and evening daily review — capture priorities, check overdue tasks, update today's log
---

# Daily Review Skill

## Morning Review (run at start of day)

1. Read today's daily log file (`logs/YYYY-MM-DD.md`). If it doesn't exist, create it from the template.
2. Check `inbox.md` — surface any items that need action today.
3. Scan active project files for tasks with `due: <today>` or `due:` dates in the past.
4. Summarise: what is due, what is overdue, what was carried forward from yesterday.
5. Ask the user to confirm their top 3 focus items for today and add them to the daily log.

## Evening Review (run at end of day)

1. Read today's daily log.
2. For each task marked `- [ ]` (unchecked), ask the user: done, carry forward, or drop?
3. Update the daily log with completions.
4. Ask if there are any notes or captures to add to `inbox.md`.
5. Close the log with a `## End of Day` section summarising what was done.

## Format for Daily Log Entry

```markdown
# YYYY-MM-DD — <Day of Week>

## Focus
- [ ] <priority 1>
- [ ] <priority 2>
- [ ] <priority 3>

## Carry Forward
↳ <task from yesterday>

## Captures
- <quick note>

## End of Day
<brief summary>
```
