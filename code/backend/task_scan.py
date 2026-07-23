"""
Deterministic active-project task scan — used by the Today view's click-to-check
task list and the morning briefing's project summary.

Public API:
    scan_open_tasks(data_root) -> list[dict]     all open tasks across active projects
    scan_todays_tasks(data_root) -> list[dict]   curated: today's materialised Daily files only
    toggle_task(data_root, rel_path, text, conn) -> bool
"""

import logging
import os
import re
from datetime import date
from pathlib import Path

import yaml

import md_editor

logger = logging.getLogger(__name__)

_SKIP_DIRS = frozenset(["db", "logs", "archive", "Archive", ".git", "__pycache__"])
_TASK_LINE_RE = re.compile(r"^(\s*)- \[( |x|X|>)\] (.+)$")
_DUE_RE = re.compile(r"\bdue:(\d{4}-\d{2}-\d{2})\b")
_PRIORITY_RE = re.compile(r"\bpriority:(high|medium|low)\b", re.IGNORECASE)
_PROJECT_REF_RE = re.compile(r"(?:^|\s)(\S+/\S+\.md)(?=\s|$)")
_MAX_TASKS = 40


# ---------------------------------------------------------------------------
# Corpus walking helpers (same shape as housekeeping.py / goal_planner.py)
# ---------------------------------------------------------------------------

def _find_ous(data_root: str) -> list[tuple[str, Path]]:
    root = Path(data_root)
    result = []
    try:
        for d in sorted(root.iterdir()):
            if d.is_dir() and d.name not in _SKIP_DIRS:
                result.append((d.name, d))
    except OSError:
        pass
    return result


def _project_files(ou_dir: Path) -> list[Path]:
    return sorted(p for p in ou_dir.glob("*.md"))


def _parse_fm(content: str) -> tuple[dict, str]:
    if content.startswith("---\n") or content.startswith("---\r\n"):
        end = content.find("\n---", 4)
        if end != -1:
            body_start = content.find("\n", end + 1) + 1
            try:
                fm = yaml.safe_load(content[4:end]) or {}
            except yaml.YAMLError:
                fm = {}
            return fm, content[body_start:]
    return {}, content


def _extract_due(text: str) -> date | None:
    m = _DUE_RE.search(text)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _clean_description(text: str) -> tuple[str, str | None, str | None]:
    """Strip recognised tags (priority:/due:/trailing project-path/carry-forward
    ↳ marker) out of a raw task-line text for display, returning
    (clean_description, priority, project_rel_path). The raw `text` itself is
    left untouched by callers — toggle_task needs it verbatim to find the line."""
    working = text
    priority = None
    m = _PRIORITY_RE.search(working)
    if m:
        priority = m.group(1).lower()
        working = working[:m.start()] + working[m.end():]
    m = _DUE_RE.search(working)
    if m:
        working = working[:m.start()] + working[m.end():]
    project = None
    m = _PROJECT_REF_RE.search(working)
    if m:
        project = m.group(1)
        working = working[:m.start()] + working[m.end():]
    working = re.sub(r"^↳\s*", "", working.strip())
    return re.sub(r"\s+", " ", working).strip(), priority, project


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_open_tasks(data_root: str) -> list[dict]:
    """
    Walk active project files, return every open task line as:
        {rel_path, text, project_title, due: 'YYYY-MM-DD' | None}
    Sorted: dated tasks ascending by due date first, then undated. Capped at _MAX_TASKS.
    """
    tasks: list[dict] = []
    for ou, ou_dir in _find_ous(data_root):
        for f in _project_files(ou_dir):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                fm, body = _parse_fm(content)
                status = str(fm.get("status") or "").strip().lower()
                if status and status != "active":
                    continue
                title = str(fm.get("title") or fm.get("key") or f.stem)
                rel_path = f"{ou}/{f.name}"

                for line in body.splitlines():
                    m = _TASK_LINE_RE.match(line)
                    if not m or m.group(2) not in (" ", ">"):
                        continue
                    text = m.group(3).strip()
                    due = _extract_due(text)
                    tasks.append({
                        "rel_path": rel_path,
                        "text": text,
                        "project_title": title,
                        "due": due.isoformat() if due else None,
                    })
            except Exception:
                logger.exception("task_scan: error reading %s", f)

    tasks.sort(key=lambda t: (t["due"] is None, t["due"] or ""))
    return tasks[:_MAX_TASKS]


def _extract_section(content: str, heading: str) -> str:
    """Body text between '## <heading>' and the next '## ' heading (or EOF)."""
    marker_re = re.compile(rf"(?m)^## {re.escape(heading)}\s*$")
    m = marker_re.search(content)
    if not m:
        return ""
    rest = content[m.end():]
    next_m = re.search(r"(?m)^## ", rest)
    return rest[:next_m.start()] if next_m else rest


def scan_todays_tasks(data_root: str) -> list[dict]:
    """
    Read <OU>/Daily/<today>.md's '## Tasks' section for every OU that has one today.
    This is the curated view: materialiser.py owns populating it (carry-forward of
    unfinished items, due-date promotion, daily/weekly Recur items) and the chat AI
    only adds to it on explicit request — unlike scan_open_tasks, this does NOT pull
    in every open checklist item from every project file. OUs with no Daily file yet
    (materialise hasn't run) contribute nothing — not an error.
    Returns {rel_path, text, ou, due}, same sort order as scan_open_tasks.
    """
    today_str = date.today().strftime("%Y-%m-%d")
    tasks: list[dict] = []
    for ou, ou_dir in _find_ous(data_root):
        daily_path = ou_dir / "Daily" / f"{today_str}.md"
        if not daily_path.is_file():
            continue
        try:
            content = daily_path.read_text(encoding="utf-8", errors="replace")
            section = _extract_section(content, "Tasks")
            rel_path = f"{ou}/Daily/{today_str}.md"
            for line in section.splitlines():
                m = _TASK_LINE_RE.match(line)
                if not m or m.group(2) not in (" ", ">"):
                    continue
                text = m.group(3).strip()
                due = _extract_due(text)
                description, priority, project = _clean_description(text)
                tasks.append({
                    "rel_path": rel_path,
                    "text": text,
                    "description": description,
                    "priority": priority,
                    "project": project,
                    "ou": ou,
                    "due": due.isoformat() if due else None,
                })
        except Exception:
            logger.exception("task_scan: error reading %s", daily_path)

    tasks.sort(key=lambda t: (t["due"] is None, t["due"] or ""))
    return tasks[:_MAX_TASKS]


def toggle_task(data_root: str, rel_path: str, text: str, conn) -> bool:
    """
    Flip a single '- [ ] <text>' line to '- [x] <text>' (exact text match) and
    auto-apply via md_editor (no confirm gate — low-risk deterministic toggle).
    Returns False if no exact match is found (e.g. file changed since the list
    was fetched) or the edit fails validation.
    """
    abs_path = Path(data_root) / rel_path
    if not abs_path.is_file():
        return False

    content = abs_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)
    target = f"- [ ] {text}"

    for i, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if stripped.strip() != target:
            continue
        ending = line[len(stripped):]
        leading_ws = stripped[:len(stripped) - len(stripped.lstrip())]
        lines[i] = f"{leading_ws}- [x] {text}{ending}"
        new_content = "".join(lines)
        try:
            proposal = md_editor.propose_edit(rel_path, new_content, f"Task done: {text[:60]}", conn)
            md_editor.apply_edit(proposal["event_id"], conn)
        except ValueError:
            logger.exception("task_scan: toggle failed validation for %s", rel_path)
            return False
        return True

    return False


def add_task(
    data_root: str,
    project_rel_path: str,
    text: str,
    priority: str | None,
    due: str | None,
    conn,
) -> bool:
    """
    Append an ad-hoc task straight into today's <OU>/Daily/<today>.md '## Tasks'
    section — tagged with the chosen project's rel_path (same trailing-path
    convention materialiser uses for Recur/Plan-sourced lines) plus optional
    priority:/due: tags. Creates today's Daily file if materialise_daily hasn't
    run yet. Auto-applied via md_editor, no confirm gate (explicit user action).
    Returns False if project_rel_path isn't a real .md file inside data_root, or
    if the write fails validation.
    """
    root = Path(data_root)
    norm_project = project_rel_path.replace("\\", "/").lstrip("/")
    project_abs = root / norm_project
    try:
        root_resolved = root.resolve()
        project_resolved = project_abs.resolve()
    except OSError:
        return False
    if not str(project_resolved).startswith(str(root_resolved) + os.sep):
        return False
    if not norm_project.endswith(".md") or not project_abs.is_file():
        return False

    # Must be a real "<OU>/<slug>.md" project file, not a root-level file like
    # ABOUT.md (no OU segment at all) or something under an auto-generated
    # subfolder (Daily/Recur/Plans/...) — those aren't projects to attach a
    # task to, and a bare root filename would make `ou` collide with an
    # existing *file* of that name, breaking the Daily-file path below.
    path_parts = norm_project.split("/")
    if len(path_parts) != 2:
        return False
    ou = path_parts[0]
    if ou not in [name for name, _ in _find_ous(data_root)]:
        return False

    today = date.today()
    rel_path = f"{ou}/Daily/{today.strftime('%Y-%m-%d')}.md"
    abs_path = root / rel_path

    parts = [text.strip()]
    if priority:
        parts.append(f"priority:{priority.lower()}")
    if due:
        parts.append(f"due:{due}")
    parts.append(norm_project)
    line = f"- [ ] {' '.join(parts)}"

    if abs_path.is_file():
        content = abs_path.read_text(encoding="utf-8", errors="replace")
        marker = "\n## Tasks\n"
        idx = content.find(marker)
        if idx == -1:
            new_content = content.rstrip("\n") + f"\n\n## Tasks\n\n{line}\n"
        else:
            insert_at = idx + len(marker)
            new_content = content[:insert_at] + line + "\n" + content[insert_at:]
    else:
        weekday = today.strftime("%A")
        new_content = (
            "---\n"
            f"date: {today.strftime('%Y-%m-%d')} {weekday}\n"
            "---\n\n"
            "## Tasks\n\n"
            f"{line}\n\n"
            "## Daily checklist\n\n\n\n"
            "## Log\n\n### Morning\n\n### Evening\n"
        )

    try:
        proposal = md_editor.propose_edit(rel_path, new_content, f"Add task: {text[:60]}", conn)
        md_editor.apply_edit(proposal["event_id"], conn)
    except (ValueError, OSError):
        logger.exception("task_scan: add_task failed for %s", rel_path)
        return False
    return True
