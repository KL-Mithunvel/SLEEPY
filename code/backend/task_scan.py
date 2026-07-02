"""
Deterministic active-project task scan — used by the Today view's click-to-check
task list and the morning briefing's project summary.

Public API:
    scan_open_tasks(data_root) -> list[dict]
    toggle_task(data_root, rel_path, text, conn) -> bool
"""

import logging
import re
from datetime import date
from pathlib import Path

import yaml

import md_editor

logger = logging.getLogger(__name__)

_SKIP_DIRS = frozenset(["db", "logs", "archive", "Archive", ".git", "__pycache__"])
_TASK_LINE_RE = re.compile(r"^(\s*)- \[( |x|X|>)\] (.+)$")
_DUE_RE = re.compile(r"\bdue:(\d{4}-\d{2}-\d{2})\b")
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
