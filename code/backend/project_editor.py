"""
Structured editing helpers for project files (<OU>/<slug>.md).

Parses/mutates the fixed project schema (see docs/CORPUS_SCHEMA.md) so the
Projects GUI can render form controls — status dropdown, task rows with
priority/due, Decisions/Open Questions lists — instead of forcing raw
markdown editing for everything. The file on disk is still plain markdown;
this module is the translation layer between that and structured JSON.

Public API:
    parse_structured(data_root, rel_path) -> dict | None
    add_task / edit_task / remove_task(data_root, rel_path, ..., conn) -> bool
    add_list_item / remove_list_item(data_root, rel_path, heading, text, conn) -> bool
    update_section_text(data_root, rel_path, heading, new_body, conn) -> bool
    set_status(data_root, rel_path, new_status, conn) -> str | None   # new rel_path, or None on failure
"""

import datetime
import logging
import os
import re

import md_editor

logger = logging.getLogger(__name__)

_STANDARD_SECTIONS = ("Goal", "Why", "Current State", "Tasks", "Decisions", "Open Questions", "Notes", "AI Notes")
_LIST_SECTIONS = ("Decisions", "Open Questions")
_VALID_STATUSES = ("active", "on_hold", "completed", "archived")

_TASK_LINE_RE = re.compile(r"^(\s*)- \[( |x|X|>)\] (.+)$")
_PRIORITY_RE = re.compile(r"\bpriority:(high|medium|low)\b", re.IGNORECASE)
_DUE_RE = re.compile(r"\bdue:(\d{4}-\d{2}-\d{2})\b")
_SECTION_HEADING_RE = re.compile(r"(?m)^## (.+?)\s*$")


# ---------------------------------------------------------------------------
# Frontmatter / section parsing (same shape as task_scan.py's helpers)
# ---------------------------------------------------------------------------

def _jsonable_fm(fm: dict) -> dict:
    """YAML auto-parses YYYY-MM-DD as date objects — stringify for JSON responses."""
    return {
        k: (v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else v)
        for k, v in fm.items()
    }


def _parse_fm(content: str) -> tuple[dict, str, int, int]:
    """Returns (frontmatter_dict, body, fm_start_line_end_offset, fm_block_end_offset)."""
    import yaml
    if content.startswith("---\n") or content.startswith("---\r\n"):
        end = content.find("\n---", 4)
        if end != -1:
            body_start = content.find("\n", end + 1) + 1
            try:
                fm = yaml.safe_load(content[4:end]) or {}
            except yaml.YAMLError:
                fm = {}
            return fm, content[body_start:], 4, body_start
    return {}, content, 0, 0


def _split_sections(body: str) -> list[dict]:
    """Ordered list of {heading, body} blocks split on '## ' headings, preserving order."""
    matches = list(_SECTION_HEADING_RE.finditer(body))
    sections = []
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append({"heading": heading, "body": body[start:end].strip("\n")})
    return sections


def _extract_section(content: str, heading: str) -> str:
    """Body text between '## <heading>' and the next '## ' heading (or EOF)."""
    marker_re = re.compile(rf"(?m)^## {re.escape(heading)}[ \t]*$")
    m = marker_re.search(content)
    if not m:
        return ""
    rest = content[m.end():]
    next_m = re.search(r"(?m)^## ", rest)
    return rest[:next_m.start()] if next_m else rest


def _parse_task_line(line: str) -> dict | None:
    m = _TASK_LINE_RE.match(line)
    if not m:
        return None
    _, mark, rest = m.groups()
    priority_m = _PRIORITY_RE.search(rest)
    due_m = _DUE_RE.search(rest)
    description = rest
    for tag_m in (priority_m, due_m):
        if tag_m:
            description = description.replace(tag_m.group(0), "")
    return {
        "line": line.rstrip(),
        "done": mark.lower() == "x",
        "description": description.strip(),
        "priority": priority_m.group(1).lower() if priority_m else None,
        "due": due_m.group(1) if due_m else None,
    }


def _build_task_line(description: str, done: bool = False, priority: str | None = None, due: str | None = None) -> str:
    mark = "x" if done else " "
    parts = [description.strip()]
    if priority:
        parts.append(f"priority:{priority.lower()}")
    if due:
        parts.append(f"due:{due}")
    return f"- [{mark}] {' '.join(parts)}"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def parse_structured(data_root: str, rel_path: str) -> dict | None:
    abs_path = os.path.join(data_root, rel_path)
    try:
        content = open(abs_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None

    fm, body, _, _ = _parse_fm(content)
    all_sections = _split_sections(body)

    sections, extra_sections, tasks, decisions, open_questions = [], [], [], [], []
    for sec in all_sections:
        heading = sec["heading"]
        if heading == "Tasks":
            for line in sec["body"].splitlines():
                parsed = _parse_task_line(line)
                if parsed:
                    tasks.append(parsed)
        elif heading == "Decisions":
            decisions = [l.lstrip("- ").strip() for l in sec["body"].splitlines() if l.strip()]
        elif heading == "Open Questions":
            open_questions = [l.lstrip("- ").strip() for l in sec["body"].splitlines() if l.strip()]
        elif heading in ("Goal", "Why", "Current State", "Notes", "AI Notes"):
            sections.append(sec)
        else:
            extra_sections.append(sec)

    title = ""
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break

    return {
        "rel_path": rel_path,
        "ou": rel_path.split("/")[0] if "/" in rel_path else "",
        "title": title,
        "frontmatter": _jsonable_fm(fm),
        "sections": {s["heading"]: s["body"] for s in sections},
        "extra_sections": extra_sections,
        "tasks": tasks,
        "decisions": decisions,
        "open_questions": open_questions,
    }


# ---------------------------------------------------------------------------
# Section replacement (shared by task/list/section mutations)
# ---------------------------------------------------------------------------

def _replace_section_body(content: str, heading: str, new_body: str) -> str | None:
    marker_re = re.compile(rf"(?m)^(## {re.escape(heading)}[ \t]*)$")
    m = marker_re.search(content)
    if not m:
        return None
    rest = content[m.end():]
    next_m = re.search(r"(?m)^## ", rest)
    tail = rest[next_m.start():] if next_m else ""
    new_body_block = new_body.rstrip("\n")
    return content[:m.end()] + "\n\n" + new_body_block + ("\n\n" if new_body_block else "\n") + tail


def _apply_full_content(data_root: str, rel_path: str, new_content: str, summary: str, conn) -> bool:
    try:
        proposal = md_editor.propose_edit(rel_path, new_content, summary, conn)
        md_editor.apply_edit(proposal["event_id"], conn)
    except ValueError:
        logger.exception("project_editor: apply failed for %s", rel_path)
        return False
    return True


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def add_task(data_root: str, rel_path: str, description: str, priority: str | None, due: str | None, conn) -> bool:
    abs_path = os.path.join(data_root, rel_path)
    if not os.path.isfile(abs_path):
        return False
    content = open(abs_path, encoding="utf-8", errors="replace").read()
    section = _extract_section(content, "Tasks")
    new_line = _build_task_line(description, priority=priority, due=due)
    new_section = (section.strip("\n") + "\n" + new_line) if section.strip() else new_line
    new_content = _replace_section_body(content, "Tasks", new_section)
    if new_content is None:
        return False
    return _apply_full_content(data_root, rel_path, new_content, f"Added task: {description[:60]}", conn)


def edit_task(
    data_root: str, rel_path: str, old_line: str,
    description: str, done: bool, priority: str | None, due: str | None, conn,
) -> bool:
    abs_path = os.path.join(data_root, rel_path)
    if not os.path.isfile(abs_path):
        return False
    lines = open(abs_path, encoding="utf-8", errors="replace").read().splitlines(keepends=True)
    target = old_line.strip()
    for i, line in enumerate(lines):
        if line.rstrip("\r\n").strip() != target:
            continue
        ending = line[len(line.rstrip("\r\n")):]
        lines[i] = _build_task_line(description, done=done, priority=priority, due=due) + ending
        new_content = "".join(lines)
        return _apply_full_content(data_root, rel_path, new_content, f"Edited task: {description[:60]}", conn)
    return False


def remove_task(data_root: str, rel_path: str, old_line: str, conn) -> bool:
    abs_path = os.path.join(data_root, rel_path)
    if not os.path.isfile(abs_path):
        return False
    lines = open(abs_path, encoding="utf-8", errors="replace").read().splitlines(keepends=True)
    target = old_line.strip()
    for i, line in enumerate(lines):
        if line.rstrip("\r\n").strip() != target:
            continue
        del lines[i]
        new_content = "".join(lines)
        return _apply_full_content(data_root, rel_path, new_content, f"Removed task: {target[:60]}", conn)
    return False


# ---------------------------------------------------------------------------
# Decisions / Open Questions (simple bullet lists)
# ---------------------------------------------------------------------------

def add_list_item(data_root: str, rel_path: str, heading: str, text: str, conn) -> bool:
    if heading not in _LIST_SECTIONS:
        return False
    abs_path = os.path.join(data_root, rel_path)
    if not os.path.isfile(abs_path):
        return False
    content = open(abs_path, encoding="utf-8", errors="replace").read()
    section = _extract_section(content, heading)
    new_line = f"- {text.strip()}"
    new_section = (section.strip("\n") + "\n" + new_line) if section.strip() else new_line
    new_content = _replace_section_body(content, heading, new_section)
    if new_content is None:
        return False
    return _apply_full_content(data_root, rel_path, new_content, f"Added to {heading}: {text[:60]}", conn)


def remove_list_item(data_root: str, rel_path: str, heading: str, text: str, conn) -> bool:
    if heading not in _LIST_SECTIONS:
        return False
    abs_path = os.path.join(data_root, rel_path)
    if not os.path.isfile(abs_path):
        return False
    content = open(abs_path, encoding="utf-8", errors="replace").read()
    section = _extract_section(content, heading)
    lines = [l for l in section.splitlines() if l.strip()]
    target = f"- {text.strip()}"
    if target not in [l.strip() for l in lines]:
        return False
    lines = [l for l in lines if l.strip() != target]
    new_content = _replace_section_body(content, heading, "\n".join(lines))
    if new_content is None:
        return False
    return _apply_full_content(data_root, rel_path, new_content, f"Removed from {heading}: {text[:60]}", conn)


# ---------------------------------------------------------------------------
# Free-form section text (Goal / Why / Current State / Notes)
# ---------------------------------------------------------------------------

def update_section_text(data_root: str, rel_path: str, heading: str, new_body: str, conn) -> bool:
    abs_path = os.path.join(data_root, rel_path)
    if not os.path.isfile(abs_path):
        return False
    content = open(abs_path, encoding="utf-8", errors="replace").read()
    new_content = _replace_section_body(content, heading, new_body)
    if new_content is None:
        return False
    return _apply_full_content(data_root, rel_path, new_content, f"Updated {heading}", conn)


# ---------------------------------------------------------------------------
# Status (may physically move the file to/from <OU>/Archive/)
# ---------------------------------------------------------------------------

def set_status(data_root: str, rel_path: str, new_status: str, conn) -> str | None:
    """Rewrite frontmatter status:, moving the file to/from <OU>/Archive/ as needed.
    Returns the (possibly new) rel_path on success, None on failure."""
    if new_status not in _VALID_STATUSES:
        return None

    abs_path = os.path.join(data_root, rel_path)
    if not os.path.isfile(abs_path):
        return None
    content = open(abs_path, encoding="utf-8", errors="replace").read()

    fm_match = re.search(r"(?m)^status:\s*\S+\s*$", content)
    if not fm_match:
        return None
    new_content = content[:fm_match.start()] + f"status: {new_status}" + content[fm_match.end():]

    parts = rel_path.split("/")
    ou, filename = parts[0], parts[-1]
    currently_archived = len(parts) >= 3 and parts[1] == "Archive"

    if new_status == "archived" and not currently_archived:
        new_rel_path = f"{ou}/Archive/{filename}"
    elif new_status != "archived" and currently_archived:
        new_rel_path = f"{ou}/{filename}"
    else:
        new_rel_path = rel_path

    if new_rel_path == rel_path:
        ok = _apply_full_content(data_root, rel_path, new_content, f"Status changed to {new_status}", conn)
        return rel_path if ok else None

    # Cross-directory move — md_editor only supports same-path overwrites, so
    # this uses git directly, same pattern as housekeeping.py's archive_old_daily.
    import git
    new_abs_path = os.path.join(data_root, new_rel_path)
    os.makedirs(os.path.dirname(new_abs_path), exist_ok=True)
    with open(new_abs_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    os.remove(abs_path)

    try:
        repo = git.Repo(data_root, search_parent_directories=False)
        repo.git.add(A=True)
        author = git.Actor("Arivu Baalan", "arivu@smtw.in")
        repo.index.commit(f"Status changed to {new_status}: {rel_path} -> {new_rel_path}", author=author, committer=author)
    except git.InvalidGitRepositoryError:
        logger.warning("project_editor: no git repo at %s, skipped commit for status move", data_root)

    return new_rel_path
