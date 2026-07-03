"""
Materialiser — nightly pipeline that seeds daily/plan files from recur templates.

Three idempotent stages:
  1. materialise_non_daily — monthly/quarterly/yearly Recur → <OU>/Plans/<period>.md
  2. materialise_daily     — seeds <OU>/Daily/<today>.md from 4 sources
  3. materialise_govern    — team-owned Recur → <OU>/Govern/<YYYY-MM>.md

Public API:
    materialise_all(data_root, user_nick='ADMIN') — runs all 3 stages for every OU found
"""

import calendar
import hashlib
import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_SKIP_DIRS = {"db", "logs", "archive", ".git", "__pycache__", "node_modules"}
_WEEKDAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def _find_ous(data_root: str) -> list[str]:
    try:
        return sorted(
            d for d in os.listdir(data_root)
            if os.path.isdir(os.path.join(data_root, d)) and d not in _SKIP_DIRS
        )
    except OSError:
        return []


def _find_recur_files(data_root: str, ou: str) -> list[Path]:
    recur_dir = Path(data_root) / ou / "Recur"
    if not recur_dir.is_dir():
        return []
    return sorted(p for p in recur_dir.glob("*.md") if p.name.lower() != "daily.md")


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
# Idempotency marker helpers
# ---------------------------------------------------------------------------

def _recur_hash(stem: str) -> str:
    return hashlib.sha1(stem.encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Schedule spec → due date
# ---------------------------------------------------------------------------

def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _last_week_first(year: int, month: int) -> int:
    return max(1, _last_day(year, month) - 6)


def _monthly_due(schedule: str | None, year: int, month: int) -> date | None:
    spec = (schedule or "day:1").strip()
    if not spec.startswith("day:"):
        return None
    day_spec = spec[4:].strip()
    if day_spec == "last":
        return date(year, month, _last_day(year, month))
    if day_spec == "last-week":
        return date(year, month, _last_week_first(year, month))
    try:
        return date(year, month, int(day_spec))
    except (ValueError, TypeError):
        return None


def _quarter_of(d: date) -> int:
    return (d.month - 1) // 3 + 1


def _quarter_start_month(q: int) -> int:
    return (q - 1) * 3 + 1


def _quarterly_due(schedule: str | None, year: int, q: int) -> date | None:
    spec = (schedule or "m1-1").strip()
    q_start = _quarter_start_month(q)

    if spec.startswith("m"):
        dash = spec.find("-")
        if dash == -1:
            return None
        m_offset = int(spec[1:dash]) - 1
        month = q_start + m_offset
        if month > 12:
            return None
        day_spec = spec[dash + 1:]
        if day_spec == "last":
            return date(year, month, _last_day(year, month))
        if day_spec == "last-week":
            return date(year, month, _last_week_first(year, month))
        try:
            return date(year, month, int(day_spec))
        except (ValueError, TypeError):
            return None

    if spec.startswith("week-of-q:"):
        week_spec = spec[len("week-of-q:"):]
        q_end_month = q_start + 2
        if week_spec == "1":
            d = date(year, q_start, 1)
            while d.weekday() != 0:
                d += timedelta(days=1)
            return d
        if week_spec == "last":
            d = date(year, q_end_month, _last_day(year, q_end_month))
            while d.weekday() != 0:
                d -= timedelta(days=1)
            return d
    return None


def _yearly_due(schedule: str | None, year: int) -> date | None:
    spec = (schedule or "day:01-01").strip()
    if not spec.startswith("day:"):
        return None
    day_spec = spec[4:].strip()
    # YYYY-MM-DD one-shot
    if re.match(r"^\d{4}-\d{2}-\d{2}$", day_spec):
        y, m, d = day_spec.split("-")
        try:
            return date(int(y), int(m), int(d))
        except ValueError:
            return None
    # MM-last
    if re.match(r"^\d{2}-last$", day_spec):
        m = int(day_spec[:2])
        return date(year, m, _last_day(year, m))
    # MM-DD
    if re.match(r"^\d{2}-\d{2}$", day_spec):
        m, d = day_spec.split("-")
        try:
            return date(year, int(m), int(d))
        except ValueError:
            return None
    return None


def _weekly_matches(schedule: str | None, today: date) -> bool:
    spec = (schedule or "weekday:mon").strip()
    if spec.startswith("weekday:"):
        days_str = spec[len("weekday:"):]
        target = set()
        for part in days_str.split(","):
            part = part.strip().lower()
            if part in _WEEKDAY_MAP:
                target.add(_WEEKDAY_MAP[part])
        return today.weekday() in target
    return today.weekday() == 0  # default Monday


def _short_month(m: int) -> str:
    return _SHORT_MONTHS[m - 1]


# ---------------------------------------------------------------------------
# Owner resolution
# ---------------------------------------------------------------------------

def _is_user_owned(fm: dict, user_nick: str) -> bool:
    owners = fm.get("owners")
    if not owners:
        return True
    if isinstance(owners, str):
        owners = [o.strip() for o in owners.split(",")]
    return user_nick.upper() in [str(o).strip().upper() for o in owners]


def _owners_list(fm: dict) -> list[str]:
    owners = fm.get("owners")
    if not owners:
        return []
    if isinstance(owners, str):
        return [o.strip() for o in owners.split(",")]
    return [str(o).strip() for o in owners]


# ---------------------------------------------------------------------------
# Bullet builder
# ---------------------------------------------------------------------------

def _build_bullet(fm: dict, recur_path: Path, data_root: str, due: date, marker: str) -> str:
    title = fm.get("title") or recur_path.stem
    rel = os.path.relpath(recur_path, data_root).replace("\\", "/")
    due_str = due.strftime("%Y-%m-%d")
    priority = str(fm.get("priority") or "")
    owners = _owners_list(fm)
    owners_str = " ".join(f"@{o}" for o in owners) if owners else ""

    parts = [f"- [ ] {title}"]
    if owners_str:
        parts.append(owners_str)
    parts.append(f"due:{due_str}")
    parts.append(rel)
    parts.append(marker)
    if priority and priority.upper() not in ("P2", ""):
        parts.append(priority.upper())
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Plan-file management
# ---------------------------------------------------------------------------

def _ensure_plans_file(plans_path: Path, period_label: str, ou: str) -> None:
    if plans_path.exists():
        content = plans_path.read_text(encoding="utf-8", errors="replace")
        if "## Recurring" not in content:
            with plans_path.open("a", encoding="utf-8") as f:
                f.write("\n## Recurring\n")
        return
    plans_path.parent.mkdir(parents=True, exist_ok=True)
    plans_path.write_text(
        f"---\ndate: {period_label}\nou: {ou}\n---\n\n"
        f"# {ou} — {period_label}\n\n"
        "## Recurring\n",
        encoding="utf-8",
    )


def _append_to_recurring(plans_path: Path, bullet: str) -> None:
    content = plans_path.read_text(encoding="utf-8", errors="replace")
    heading = "\n## Recurring"
    idx = content.rfind(heading)
    if idx == -1:
        content += f"\n## Recurring\n{bullet}\n"
        plans_path.write_text(content, encoding="utf-8")
        return
    section_start = content.find("\n", idx + 1) + 1
    next_heading = content.find("\n## ", section_start)
    if next_heading == -1:
        # Append at end of file
        if not content.endswith("\n"):
            content += "\n"
        content += bullet + "\n"
    else:
        content = content[:next_heading] + f"\n{bullet}" + content[next_heading:]
    plans_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Stage 1: materialise_non_daily — Recur → Plans
# ---------------------------------------------------------------------------

def materialise_non_daily(
    data_root: str, user_nick: str = "ADMIN", today: date | None = None
) -> dict:
    if today is None:
        today = date.today()

    stats = {"ous": 0, "processed": 0, "written": 0, "skipped": 0}
    for ou in _find_ous(data_root):
        stats["ous"] += 1
        for recur_path in _find_recur_files(data_root, ou):
            try:
                content = recur_path.read_text(encoding="utf-8", errors="replace")
                fm, _ = _parse_fm(content)
                cadence = str(fm.get("cadence") or "").strip().lower()

                if cadence not in ("monthly", "quarterly", "yearly"):
                    continue
                if not _is_user_owned(fm, user_nick):
                    continue

                stats["processed"] += 1
                schedule = str(fm.get("schedule") or "")
                h = _recur_hash(recur_path.stem)

                if cadence == "monthly":
                    period_label = f"{today.year}-{today.month:02d}"
                    period_suffix = f"M{today.month:02d}"
                    plans_path = Path(data_root) / ou / "Plans" / f"{period_label}.md"
                    due = _monthly_due(schedule, today.year, today.month)

                elif cadence == "quarterly":
                    q = _quarter_of(today)
                    period_label = f"{today.year}-Q{q}"
                    period_suffix = f"Q{q}"
                    plans_path = Path(data_root) / ou / "Plans" / f"{period_label}.md"
                    due = _quarterly_due(schedule, today.year, q)

                else:  # yearly
                    period_label = str(today.year)
                    period_suffix = "Y"
                    plans_path = Path(data_root) / ou / "Plans" / f"{period_label}.md"
                    due = _yearly_due(schedule, today.year)
                    # One-shot dates in a different year: skip
                    if due and due.year != today.year:
                        continue

                if due is None:
                    logger.warning("Cannot compute due date: %s (cadence=%s schedule=%s)",
                                   recur_path, cadence, schedule)
                    continue

                marker = f"^R:{h}-{period_suffix}"
                _ensure_plans_file(plans_path, period_label, ou)
                if marker in plans_path.read_text(encoding="utf-8", errors="replace"):
                    stats["skipped"] += 1
                    continue

                bullet = _build_bullet(fm, recur_path, data_root, due, marker)
                _append_to_recurring(plans_path, bullet)
                stats["written"] += 1
                logger.info("Non-daily: %s → %s (%s)", recur_path.name, plans_path.name, marker)

            except Exception:
                logger.exception("Error processing %s", recur_path)

    return stats


# ---------------------------------------------------------------------------
# Stage 2: materialise_daily — seeds Daily/<today>.md
# ---------------------------------------------------------------------------

def _all_daily_files(ou_path: Path) -> list[Path]:
    d = ou_path / "Daily"
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("????-??-??.md"))


def _date_from_stem(p: Path) -> date | None:
    try:
        y, m, d = p.stem.split("-")
        return date(int(y), int(m), int(d))
    except (ValueError, AttributeError):
        return None


def _section_lines(content: str, heading: str) -> list[str]:
    in_sec = False
    out: list[str] = []
    for line in content.splitlines():
        if line.startswith("## "):
            if in_sec:
                break
            if line.strip() == f"## {heading}":
                in_sec = True
        elif in_sec:
            out.append(line)
    return out


def _carry_forward(data_root: str, ou: str, today: date) -> list[str]:
    ou_path = Path(data_root) / ou
    prev = None
    for p in reversed(_all_daily_files(ou_path)):
        d = _date_from_stem(p)
        if d and d < today:
            prev = p
            break
    if not prev:
        return []

    content = prev.read_text(encoding="utf-8", errors="replace")
    result = []
    for line in _section_lines(content, "Tasks"):
        if "- [ ]" in line:
            # Strip existing ↳, then re-add
            stripped = re.sub(r"^(\s*- \[ \] )↳ ?", r"\1", line)
            stripped = re.sub(r"^(\s*- \[ \] )", r"\1↳ ", stripped)
            result.append(stripped)
    return result


_DUE_START_RE = re.compile(
    r"\b(start|due):(\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2}-\d{2})\b"
)


def _matches_today(line: str, today: date) -> bool:
    iso = today.strftime("%Y-%m-%d")
    short = f"{_short_month(today.month)}-{today.day:02d}"
    for m in _DUE_START_RE.finditer(line):
        v = m.group(2)
        if v == iso or v == short:
            return True
    return False


def _apply_plan_pipe(data_root: str, ou: str, today: date) -> list[str]:
    result: list[str] = []
    plans_dir = Path(data_root) / ou / "Plans"
    if not plans_dir.is_dir():
        return result

    for plan_file in sorted(plans_dir.glob("*.md")):
        content = plan_file.read_text(encoding="utf-8", errors="replace")
        new_lines = []
        modified = False
        for line in content.splitlines(keepends=True):
            if "- [ ]" in line and _matches_today(line, today):
                new_lines.append(line.replace("- [ ]", "- [>]", 1))
                modified = True
                result.append(line.rstrip())
            else:
                new_lines.append(line)
        if modified:
            plan_file.write_text("".join(new_lines), encoding="utf-8")

    return result


def _recur_slug(recur_path: Path, data_root: str) -> str:
    return os.path.relpath(recur_path, data_root).replace("\\", "/")


def _weekly_tasks(data_root: str, ou: str, today: date, existing: str) -> list[str]:
    result: list[str] = []
    for recur_path in _find_recur_files(data_root, ou):
        try:
            content = recur_path.read_text(encoding="utf-8", errors="replace")
            fm, _ = _parse_fm(content)
            if str(fm.get("cadence") or "").strip().lower() != "weekly":
                continue
            if not _weekly_matches(fm.get("schedule"), today):
                continue
            slug = _recur_slug(recur_path, data_root)
            if slug in existing:
                continue
            title = fm.get("title") or recur_path.stem
            owners = _owners_list(fm)
            owners_str = " ".join(f"@{o}" for o in owners) if owners else ""
            parts = [f"- [ ] {title}"]
            if owners_str:
                parts.append(owners_str)
            parts.append(f"due:{today.strftime('%Y-%m-%d')}")
            parts.append(slug)
            result.append(" ".join(parts))
        except Exception:
            logger.exception("Weekly recur error: %s", recur_path)
    return result


def _daily_tasks(data_root: str, ou: str, today: date, existing: str) -> list[str]:
    """Recur files with cadence: daily — one task line added every day, same shape
    as _weekly_tasks but unconditional (no weekday match needed)."""
    result: list[str] = []
    for recur_path in _find_recur_files(data_root, ou):
        try:
            content = recur_path.read_text(encoding="utf-8", errors="replace")
            fm, _ = _parse_fm(content)
            if str(fm.get("cadence") or "").strip().lower() != "daily":
                continue
            slug = _recur_slug(recur_path, data_root)
            if slug in existing:
                continue
            title = fm.get("title") or recur_path.stem
            owners = _owners_list(fm)
            owners_str = " ".join(f"@{o}" for o in owners) if owners else ""
            parts = [f"- [ ] {title}"]
            if owners_str:
                parts.append(owners_str)
            parts.append(f"due:{today.strftime('%Y-%m-%d')}")
            parts.append(slug)
            result.append(" ".join(parts))
        except Exception:
            logger.exception("Daily recur error: %s", recur_path)
    return result


def _daily_checklist(data_root: str, ou: str) -> list[str]:
    f = Path(data_root) / ou / "Recur" / "Daily.md"
    if not f.is_file():
        return []
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    result = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("---"):
            continue
        if line.startswith("-"):
            result.append(line)
        else:
            result.append(f"- [ ] {line}")
    return result


def _create_daily(today: date, tasks: list[str], checklist: list[str]) -> str:
    weekday = today.strftime("%A")
    lines = [
        "---",
        f"date: {today.strftime('%Y-%m-%d')} {weekday}",
        "---",
        "",
        "## Tasks",
        "",
    ]
    lines.extend(tasks or [""])
    lines += ["", "## Daily checklist", ""]
    lines.extend(checklist or [""])
    lines += ["", "## Log", "", "### Morning", "", "### Evening", ""]
    return "\n".join(lines)


def _merge_daily(existing: str, new_tasks: list[str], new_checklist: list[str]) -> str:
    existing_set = set(existing.splitlines())
    content = existing

    def _insert_after_heading(text: str, heading: str, items: list[str]) -> str:
        marker = f"\n## {heading}\n"
        idx = text.find(marker)
        if idx == -1:
            return text + f"\n## {heading}\n" + "\n".join(items) + "\n"
        insert_at = idx + len(marker)
        return text[:insert_at] + "\n".join(items) + "\n" + text[insert_at:]

    fresh_tasks = [t for t in new_tasks if t not in existing_set]
    if fresh_tasks:
        content = _insert_after_heading(content, "Tasks", fresh_tasks)

    fresh_checklist = [c for c in new_checklist if c not in existing_set]
    if fresh_checklist:
        content = _insert_after_heading(content, "Daily checklist", fresh_checklist)

    return content


def materialise_daily(
    data_root: str, user_nick: str = "ADMIN", today: date | None = None
) -> dict:
    if today is None:
        today = date.today()

    stats = {"ous": 0, "created": 0, "merged": 0}
    for ou in _find_ous(data_root):
        stats["ous"] += 1
        ou_path = Path(data_root) / ou
        daily_dir = ou_path / "Daily"
        daily_path = daily_dir / f"{today.strftime('%Y-%m-%d')}.md"

        existing = daily_path.read_text(encoding="utf-8", errors="replace") if daily_path.is_file() else ""

        carry = _carry_forward(data_root, ou, today)
        pipe = _apply_plan_pipe(data_root, ou, today)
        daily_recur = _daily_tasks(data_root, ou, today, existing)
        weekly = _weekly_tasks(data_root, ou, today, existing)
        checklist = _daily_checklist(data_root, ou)

        all_tasks = carry + pipe + daily_recur + weekly

        if not daily_path.is_file():
            daily_dir.mkdir(parents=True, exist_ok=True)
            daily_path.write_text(_create_daily(today, all_tasks, checklist), encoding="utf-8")
            stats["created"] += 1
        else:
            merged = _merge_daily(existing, all_tasks, checklist)
            if merged != existing:
                daily_path.write_text(merged, encoding="utf-8")
                stats["merged"] += 1

        logger.info("Daily: ou=%s tasks=%d checklist=%d", ou, len(all_tasks), len(checklist))

    return stats


# ---------------------------------------------------------------------------
# Stage 3: materialise_govern — team-owned Recur → Govern/<YYYY-MM>.md
# ---------------------------------------------------------------------------

def _ensure_govern(govern_path: Path, month_label: str) -> None:
    if govern_path.exists():
        return
    govern_path.parent.mkdir(parents=True, exist_ok=True)
    govern_path.write_text(f"# Govern — {month_label}\n", encoding="utf-8")


def _append_govern_owner(govern_path: Path, owner: str, bullet: str) -> None:
    content = govern_path.read_text(encoding="utf-8", errors="replace")
    section = f"\n## @{owner}"
    if section not in content:
        if not content.endswith("\n"):
            content += "\n"
        content += f"{section}\n\n{bullet}\n"
    else:
        idx = content.index(section)
        end = content.find("\n## ", idx + 1)
        if end == -1:
            if not content.endswith("\n"):
                content += "\n"
            content += bullet + "\n"
        else:
            content = content[:end] + f"\n{bullet}" + content[end:]
    govern_path.write_text(content, encoding="utf-8")


def materialise_govern(
    data_root: str, user_nick: str = "ADMIN", today: date | None = None
) -> dict:
    if today is None:
        today = date.today()

    stats = {"ous": 0, "written": 0, "skipped": 0}
    month_label = today.strftime("%Y-%m")

    for ou in _find_ous(data_root):
        stats["ous"] += 1
        for recur_path in _find_recur_files(data_root, ou):
            try:
                content = recur_path.read_text(encoding="utf-8", errors="replace")
                fm, _ = _parse_fm(content)
                cadence = str(fm.get("cadence") or "").strip().lower()

                if cadence not in ("monthly", "quarterly", "yearly", "weekly"):
                    continue
                if _is_user_owned(fm, user_nick):
                    continue

                owners = _owners_list(fm)
                if not owners:
                    continue

                govern_path = Path(data_root) / ou / "Govern" / f"{month_label}.md"
                _ensure_govern(govern_path, month_label)
                existing = govern_path.read_text(encoding="utf-8", errors="replace")

                h = _recur_hash(recur_path.stem)

                if cadence == "monthly":
                    period_suffix = f"M{today.month:02d}"
                    marker = f"^R:{h}-{period_suffix}"
                    if marker in existing:
                        stats["skipped"] += 1
                        continue
                    due = _monthly_due(fm.get("schedule"), today.year, today.month)
                    if not due:
                        continue
                    for owner in owners:
                        bullet = _build_bullet(fm, recur_path, data_root, due, marker)
                        _append_govern_owner(govern_path, owner, bullet)
                        stats["written"] += 1

                elif cadence == "weekly":
                    slug = _recur_slug(recur_path, data_root)
                    if slug in existing:
                        stats["skipped"] += 1
                        continue
                    if not _weekly_matches(fm.get("schedule"), today):
                        continue
                    title = fm.get("title") or recur_path.stem
                    due_str = today.strftime("%Y-%m-%d")
                    for owner in owners:
                        bullet = f"- [ ] {title} @{owner} due:{due_str} {slug}"
                        _append_govern_owner(govern_path, owner, bullet)
                        stats["written"] += 1

                elif cadence in ("quarterly", "yearly"):
                    period_suffix = f"Q{_quarter_of(today)}" if cadence == "quarterly" else "Y"
                    marker = f"^R:{h}-{period_suffix}"
                    if marker in existing:
                        stats["skipped"] += 1
                        continue
                    if cadence == "quarterly":
                        due = _quarterly_due(fm.get("schedule"), today.year, _quarter_of(today))
                    else:
                        due = _yearly_due(fm.get("schedule"), today.year)
                    if not due:
                        continue
                    for owner in owners:
                        bullet = _build_bullet(fm, recur_path, data_root, due, marker)
                        _append_govern_owner(govern_path, owner, bullet)
                        stats["written"] += 1

            except Exception:
                logger.exception("Govern error: %s", recur_path)

    return stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def materialise_all(data_root: str, user_nick: str = "ADMIN") -> dict:
    today = date.today()
    logger.info("materialise_all: data_root=%s user_nick=%s date=%s", data_root, user_nick, today)
    s1 = materialise_non_daily(data_root, user_nick, today)
    s2 = materialise_daily(data_root, user_nick, today)
    s3 = materialise_govern(data_root, user_nick, today)
    result = {"date": str(today), "non_daily": s1, "daily": s2, "govern": s3}
    logger.info("materialise_all done: %s", result)
    return result
