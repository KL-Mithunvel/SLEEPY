"""
Goal planner — nightly deadline awareness for projects with a target_date.

For every active project carrying a `target_date: YYYY-MM-DD` frontmatter field:
  - Calls the LLM to (re)generate a '## Plan' section (this week's goal + today's
    next action, grounded in the project's own content).
  - Replaces that section in the project file and commits the change.
  - Builds a deterministic (non-LLM) one-line digest entry, sorted by urgency,
    for the caller to fold into the morning-briefing email.

Public API:
    run_goal_planning(data_root, user_nick='ADMIN') -> dict
"""

import logging
import re
from datetime import date
from pathlib import Path

import yaml

import ai_client

logger = logging.getLogger(__name__)

_SKIP_DIRS = frozenset(["db", "logs", "archive", "Archive", ".git", "__pycache__"])
_MAX_PROJECT_CHARS = 8000


# ---------------------------------------------------------------------------
# Corpus walking helpers (same shape as housekeeping.py / materialiser.py)
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
    """Direct .md children of an OU dir (project files sit at OU root)."""
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


# ---------------------------------------------------------------------------
# LLM plan generation
# ---------------------------------------------------------------------------

_PLAN_SYSTEM = """\
You are a focused project-planning assistant. Given a project's current notes and a \
target completion date, produce ONLY the body of a "## Plan" markdown section — do not \
include the heading itself. Include:
- **This week's goal:** one sentence describing the concrete outcome to aim for this week.
- **Today's next action:** one concrete, doable-today task.
- If more than 14 days remain until the target date, also add a short week-by-week \
milestone list (one line per week, current week first).
Ground everything in the provided project content — never invent tasks, people, or \
context that isn't there. Keep it under 200 words. Output plain Markdown only, no preamble.\
"""

_TODAY_ACTION_RE = re.compile(r"\*\*Today'?s? next action:?\*\*\s*(.+)")


def _generate_plan(
    title: str, project_content: str, target_date: date, today: date, days_remaining: int
) -> str | None:
    user_msg = (
        f"Project: {title}\n"
        f"Target date: {target_date.isoformat()}\n"
        f"Today: {today.isoformat()}\n"
        f"Days remaining: {days_remaining}\n\n"
        f"{project_content[:_MAX_PROJECT_CHARS]}"
    )
    messages = [
        {"role": "system", "content": _PLAN_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    try:
        resp = ai_client.chat(messages, event_type="goal_planning")
        return resp["content"].strip()
    except Exception:
        logger.exception("goal_planner: LLM call failed for %r", title)
        return None


def _extract_today_action(plan_body: str) -> str | None:
    m = _TODAY_ACTION_RE.search(plan_body)
    if not m:
        return None
    return m.group(1).strip().rstrip(".")


# ---------------------------------------------------------------------------
# Section replace
# ---------------------------------------------------------------------------

def _replace_section(content: str, heading: str, new_body: str) -> str:
    """
    Replace the body of a '## <heading>' section with new_body (everything between
    this heading and the next '## ' heading, or end of file). If the heading doesn't
    exist yet, insert it before '## AI Notes' (or append at end if that's absent too).
    """
    new_body = new_body.strip()
    heading_re = re.compile(rf"(?m)^## {re.escape(heading)}\s*$")
    m = heading_re.search(content)
    if m:
        section_start = m.end()
        rest = content[section_start:]
        next_m = re.search(r"(?m)^## ", rest)
        if next_m:
            section_end = section_start + next_m.start()
            return content[:section_start] + f"\n\n{new_body}\n\n" + content[section_end:]
        return content[:section_start].rstrip("\n") + f"\n\n{new_body}\n"

    block = f"## {heading}\n\n{new_body}\n\n"
    anchor = re.search(r"(?m)^## AI Notes\s*$", content)
    if anchor:
        return content[:anchor.start()] + block + content[anchor.start():]
    if not content.endswith("\n"):
        content += "\n"
    return content + f"\n{block}"


# ---------------------------------------------------------------------------
# Digest line formatting (deterministic — no LLM involved)
# ---------------------------------------------------------------------------

def _format_digest_line(
    title: str, target_date: date, days_remaining: int, today_action: str | None
) -> str:
    if days_remaining < 0:
        n = abs(days_remaining)
        urgency = f"⚠️ OVERDUE by {n} day{'s' if n != 1 else ''}"
    elif days_remaining == 0:
        urgency = "⏰ due today"
    else:
        urgency = f"⏰ {days_remaining} day{'s' if days_remaining != 1 else ''} left"

    line = f"**{title}** — {urgency} (target {target_date.isoformat()})"
    if today_action:
        line += f" — Today: {today_action}"
    return line


# ---------------------------------------------------------------------------
# Git commit
# ---------------------------------------------------------------------------

def _commit_goal_planning(data_root: str, count: int) -> None:
    try:
        import git
        from datetime import datetime, timezone, timedelta as td
        _IST = timezone(td(hours=5, minutes=30))
        ts = datetime.now(_IST).strftime("%Y-%m-%d")
        try:
            repo = git.Repo(data_root, search_parent_directories=False)
        except git.InvalidGitRepositoryError:
            return
        repo.git.add(A=True)
        if not repo.git.status("--porcelain").strip():
            return
        author = git.Actor("Arivu Baalan", "arivu@smtw.in")
        repo.index.commit(
            f"goal-planning: updated {count} project plan(s) ({ts})",
            author=author,
            committer=author,
        )
        logger.info("goal_planner: committed %d update(s)", count)
    except Exception:
        logger.warning("goal_planner: git commit failed (files updated on disk)")


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

def run_goal_planning(data_root: str, user_nick: str = "ADMIN") -> dict:
    """
    Scan active projects with a target_date, refresh their '## Plan' section via LLM,
    commit changes, and return a digest sorted by urgency (soonest/most overdue first).
    """
    today = date.today()
    stats = {"scanned": 0, "updated": 0, "digest_lines": [], "errors": 0}
    changed_files: list[Path] = []
    ranked_digest: list[tuple[int, str]] = []

    for ou, ou_dir in _find_ous(data_root):
        for f in _project_files(ou_dir):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                if not (content.startswith("---\n") or content.startswith("---\r\n")):
                    continue
                fm, _ = _parse_fm(content)
                if str(fm.get("status") or "").strip().lower() != "active":
                    continue
                target_raw = fm.get("target_date")
                if not target_raw:
                    continue
                try:
                    target_date = date.fromisoformat(str(target_raw).strip())
                except ValueError:
                    logger.warning("goal_planner: invalid target_date in %s: %r", f, target_raw)
                    continue

                stats["scanned"] += 1
                days_remaining = (target_date - today).days
                title = str(fm.get("title") or fm.get("key") or f.stem)

                plan_body = _generate_plan(title, content, target_date, today, days_remaining)
                if plan_body is None:
                    stats["errors"] += 1
                    continue

                new_content = _replace_section(content, "Plan", plan_body)
                if new_content != content:
                    f.write_text(new_content, encoding="utf-8")
                    stats["updated"] += 1
                    changed_files.append(f)

                today_action = _extract_today_action(plan_body)
                line = _format_digest_line(title, target_date, days_remaining, today_action)
                ranked_digest.append((days_remaining, line))

            except Exception:
                logger.exception("goal_planner: error processing %s", f)
                stats["errors"] += 1

    ranked_digest.sort(key=lambda pair: pair[0])
    stats["digest_lines"] = [line for _, line in ranked_digest]

    if changed_files:
        _commit_goal_planning(data_root, len(changed_files))

    logger.info(
        "goal_planner: scanned=%d updated=%d errors=%d",
        stats["scanned"], stats["updated"], stats["errors"],
    )
    return stats
