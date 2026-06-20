# Playbook Format

A playbook is a sequence of recurring tasks tied to a project, with a defined cadence
and schedule. Unlike `Recur/` files (which are global), playbooks live inside project
files under the `## Playbook` section.

## Syntax

```markdown
## Playbook

| Cadence | Schedule | Task |
|---------|----------|------|
| monthly | first monday | Send monthly status report to stakeholders |
| weekly | friday | Update project task log |
| quarterly | first monday | Review OKRs and adjust goals |
```

## Cadence and Schedule

Same grammar as Recur files (see `Recur-Format.md`).

## Ownership

Playbook tasks inherit the project's `owner:` frontmatter field unless overridden
with `@nick` in the task text.

## Materialiser Behaviour

The `materialise_non_daily` stage reads playbooks and injects due tasks into the
appropriate plan file (`Plans/YYYY/WNN/YYYY-MM-DD.md`) with a `^P` marker.

## Disambiguation: Recur vs Playbook

| | Recur | Playbook |
|--|-------|---------|
| Scope | Global or OU-wide | Tied to a single project |
| Location | `Recur/` directory | `## Playbook` section in project file |
| Use for | Universal habits, team rituals | Project-specific recurring work |
