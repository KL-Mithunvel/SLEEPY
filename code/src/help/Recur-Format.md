# Recur File Format

Recur files define recurring tasks that materialise into daily and plan files automatically.

## Location

`<OU>/Recur/<filename>.md` or `Recur/Daily.md` for global daily checklist items.

## Frontmatter

```yaml
---
title: Monthly Compliance Check
cadence: monthly
schedule: first monday
owners: [klm]
priority: high
---
```

### `cadence` values

| Value | Meaning |
|-------|---------|
| `daily` | Every day |
| `weekly` | Every week (use `schedule: monday` etc.) |
| `monthly` | Every month |
| `quarterly` | Every quarter |
| `yearly` | Every year |

### `schedule` grammar

| Cadence | Example schedule | Meaning |
|---------|-----------------|---------|
| weekly | `monday` | Every Monday |
| monthly | `first monday` | First Monday of the month |
| monthly | `15` | 15th of every month |
| monthly | `last friday` | Last Friday of the month |
| quarterly | `first monday` | First Monday of each quarter |
| yearly | `01-04` | April 1st every year |

### `owners`

List of usernames (local part of email). `klm` = KL Mithunvel. Tasks with other owners
route to the Govern view rather than the daily log.

## Body

The body is a standard Markdown task list. Each `- [ ]` item will be injected into
the appropriate daily or plan file on the materialiser run.

```markdown
- [ ] Review open invoices
- [ ] Send weekly status update to team
```

## `^R` Marker

The materialiser inserts a `^R YYYY-MM-DD` marker after each injected task to track
when it was last materialised. Do not edit these markers manually.

## `Recur/Daily.md`

A flat task list (no frontmatter) that is copied into every daily log as a checklist:

```markdown
- [ ] Check inbox
- [ ] Review calendar
- [ ] Update daily log before EOD
```
