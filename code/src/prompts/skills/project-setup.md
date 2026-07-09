---
name: project-setup
description: Initialize a new project — create the project file with proper frontmatter, structure, and first tasks
---

# Project Setup Skill

## Step 1 — Gather Information

Ask the user (in one message, not separately):
1. Project title and which OU (Organisational Unit) it belongs to
2. What is the project trying to achieve? (1-2 sentences)
3. What are the first 3-5 tasks to get started?
4. Any due date or deadline?
5. Any people to flag (`@nick` in People.md)?

## Step 2 — Create the Project File

Generate the file at `<OU>/<project-slug>.md` using the standard format:

```markdown
---
key: <OU>-<SLUG>
title: <Project Title>
status: active
priority: normal
owner: klm
---

# <Project Title>

## Purpose
<one paragraph — what this project achieves and why it matters>

## Goals
- <goal 1>
- <goal 2>

## Tasks
- [ ] <first task>
- [ ] <second task>

## Notes
<any initial context, links, or constraints>

## AI Notes

## Log
```

## Step 3 — Confirm

Show the user a preview of the file content and ask for approval before creating it.
After approval, create the file using a pma-edit block (empty SEARCH = new file).

## Naming Rules

- OU: uppercase abbreviation (e.g. `SMTW`, `Infra`, `Personal`)
- Slug: lowercase, hyphenated (e.g. `finance-review`, `infra-proxmox`)
- Key format: `<OU>-<SLUG>` (e.g. `SMTW-FINANCE-REVIEW`)
