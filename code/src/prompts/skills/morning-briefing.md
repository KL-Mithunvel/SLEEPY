---
name: morning-briefing
description: Generate a morning briefing — surface today's tasks, overdue items, and a recommended focus plan
---

# Morning Briefing Skill

## Steps

1. Search the corpus for: active tasks due today or overdue, blocked projects, recently updated items.
2. Read `inbox.md` to surface any unprocessed captures.
3. Read today's daily log if it exists; if not, note that it needs to be created.
4. Generate a concise briefing:

```markdown
# Morning Briefing — <date>

## Today's Focus (recommended top 3)
1. <item>
2. <item>
3. <item>

## Due Today
- <task> — [<project>]

## Overdue
- <task> (was due <date>) — [<project>]

## Inbox
- <N> items pending — oldest: <summary>

## Blocked / At Risk
- <project>: <reason>
```

## Rules

- Only surface what is in the corpus — never invent tasks.
- If no tasks are due today, say so explicitly (good news!).
- Keep the briefing readable in under 2 minutes.
- If the daily log for today doesn't exist, offer to create it after the briefing.
