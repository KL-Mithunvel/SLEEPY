"""
Housekeeping — nightly corpus health checks and archiving.

Two types of work:
  Checkers  (read-only) — scan the corpus and emit Finding objects
  Actions   (mutating)  — archive_old_daily moves Daily files > 365 days old

Findings are written to <data_root>/inbox.md under ## Housekeeping with
exact-line deduplication so repeated runs don't flood the inbox.

Public API:
    run_housekeeping(data_root, user_nick='ADMIN') -> dict
"""

import logging
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Literal

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    severity: Literal["info", "warn"]
    summary: str
    location: str | None = None   # "OU/file.md:42" or None for repo-level


# ---------------------------------------------------------------------------
# Constants / regex
# ---------------------------------------------------------------------------

_TASK_LINE_RE = re.compile(r"^\s*- \[( |x|X|>)\] (.+)$")
_OWNER_RE = re.compile(r"@([A-Za-z][\w-]*)")
_QUEUE_RE = re.compile(r"-QUEUE(?:-|$)", re.IGNORECASE)
_DATE_TOKEN_RE = re.compile(r"\b(start|finish|due):(\S+)")

_SHORT_MONTHS = frozenset(
    ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
)

_SKIP_OU_DIRS = frozenset(
    ["db", "logs", "archive", "Archive", ".git", "__pycache__"]
)
_SKIP_ARCHIVE_DIRS = frozenset(["Archive", "archive"])

_PROJECT_REQUIRED_FIELDS = ("key", "status")
_RECUR_REQUIRED_FIELDS = ("cadence",)


# ---------------------------------------------------------------------------
# Corpus walking helpers
# ---------------------------------------------------------------------------

def _find_ous(data_root: str) -> list[tuple[str, Path]]:
    root = Path(data_root)
    result = []
    try:
        for d in sorted(root.iterdir()):
            if d.is_dir() and d.name not in _SKIP_OU_DIRS:
                result.append((d.name, d))
    except OSError:
        pass
    return result


def _project_files(ou_dir: Path) -> list[Path]:
    """Direct .md children of an OU dir (project files sit at OU root)."""
    return sorted(p for p in ou_dir.glob("*.md"))


def _all_md_files(ou_dir: Path) -> list[tuple[Path, str]]:
    """
    All .md files in ou_dir tree excluding Archive/ subtrees.
    Returns (abs_path, ou-relative posix path) pairs.
    """
    results: list[tuple[Path, str]] = []
    for root, dirs, files in os.walk(ou_dir):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_ARCHIVE_DIRS)
        root_p = Path(root)
        for fname in sorted(files):
            if fname.endswith(".md"):
                abs_p = root_p / fname
                rel = abs_p.relative_to(ou_dir).as_posix()
                results.append((abs_p, rel))
    return results


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
# Checker registry
# ---------------------------------------------------------------------------

_CHECKERS: list[tuple[str, Callable]] = []


def register_checker(name: str):
    def decorator(fn):
        _CHECKERS.append((name, fn))
        return fn
    return decorator


# ---------------------------------------------------------------------------
# Checker 1 — project_status_hygiene
# ---------------------------------------------------------------------------

@register_checker("project_status_hygiene")
def check_project_status_hygiene(data_root: str, user_nick: str) -> list[Finding]:
    """Active projects where all tasks are resolved → nudge to close them."""
    findings: list[Finding] = []
    for ou, ou_dir in _find_ous(data_root):
        for f in _project_files(ou_dir):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                fm, body = _parse_fm(content)
                if str(fm.get("status") or "").strip().lower() != "active":
                    continue
                key = str(fm.get("key") or f.stem)
                if _QUEUE_RE.search(key):
                    continue

                unchecked = checked = 0
                for line in body.splitlines():
                    m = _TASK_LINE_RE.match(line)
                    if m:
                        if m.group(1) in (" ", ">"):
                            unchecked += 1
                        else:
                            checked += 1

                if checked > 0 and unchecked == 0:
                    findings.append(Finding(
                        severity="warn",
                        summary=(
                            f"{key}: all tasks done — "
                            "mark `status: completed` or add next-step tasks"
                        ),
                        location=f"{ou}/{f.name}",
                    ))
            except Exception:
                logger.exception("project_status_hygiene error: %s", f)
    return findings


# ---------------------------------------------------------------------------
# Checker 2 — missing_frontmatter
# ---------------------------------------------------------------------------

@register_checker("missing_frontmatter")
def check_missing_frontmatter(data_root: str, user_nick: str) -> list[Finding]:
    """Required YAML frontmatter fields missing from project or recur files."""
    findings: list[Finding] = []
    for ou, ou_dir in _find_ous(data_root):
        for f in _project_files(ou_dir):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                if not (content.startswith("---\n") or content.startswith("---\r\n")):
                    findings.append(Finding(
                        severity="warn",
                        summary="missing YAML frontmatter block",
                        location=f"{ou}/{f.name}",
                    ))
                    continue
                fm, _ = _parse_fm(content)
                for field in _PROJECT_REQUIRED_FIELDS:
                    if not fm.get(field):
                        findings.append(Finding(
                            severity="warn",
                            summary=f"missing `{field}:` in project frontmatter",
                            location=f"{ou}/{f.name}",
                        ))
            except Exception:
                logger.exception("missing_frontmatter project error: %s", f)

        recur_dir = ou_dir / "Recur"
        if recur_dir.is_dir():
            for f in sorted(recur_dir.glob("*.md")):
                if f.name.lower() == "daily.md":
                    continue
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    fm, _ = _parse_fm(content)
                    for field in _RECUR_REQUIRED_FIELDS:
                        if not fm.get(field):
                            findings.append(Finding(
                                severity="warn",
                                summary=f"missing `{field}:` in recur frontmatter",
                                location=f"{ou}/Recur/{f.name}",
                            ))
                except Exception:
                    logger.exception("missing_frontmatter recur error: %s", f)
    return findings


# ---------------------------------------------------------------------------
# Checker 3 — unknown_owner
# ---------------------------------------------------------------------------

def _valid_nicks(data_root: str, ou: str, user_nick: str) -> frozenset[str]:
    """Known uppercase @nicks: user + root People.md + <OU>/People/ stems."""
    valid: set[str] = {user_nick.upper()}

    people_md = Path(data_root) / "People.md"
    if people_md.is_file():
        try:
            text = people_md.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"@([A-Z][A-Z0-9_-]{1,})", text):
                valid.add(m.group(1))
            for m in re.finditer(r"^nick:\s*(\w+)", text, re.MULTILINE):
                valid.add(m.group(1).upper())
        except Exception:
            pass

    people_dir = Path(data_root) / ou / "People"
    if people_dir.is_dir():
        for f in people_dir.glob("*.md"):
            valid.add(f.stem.upper())
            try:
                fm, _ = _parse_fm(f.read_text(encoding="utf-8", errors="replace"))
                if fm.get("nick"):
                    valid.add(str(fm["nick"]).upper())
            except Exception:
                pass

    return frozenset(valid)


_ALL_UPPER_NICK_RE = re.compile(r"^[A-Z][A-Z0-9_-]+$")


@register_checker("unknown_owner")
def check_unknown_owner(data_root: str, user_nick: str) -> list[Finding]:
    """Uppercase @NICK on task lines not resolved to a known person."""
    findings: list[Finding] = []
    for ou, ou_dir in _find_ous(data_root):
        valid = _valid_nicks(data_root, ou, user_nick)
        for abs_path, rel in _all_md_files(ou_dir):
            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
                for lineno, line in enumerate(content.splitlines(), 1):
                    if not _TASK_LINE_RE.match(line):
                        continue
                    for m in _OWNER_RE.finditer(line):
                        nick = m.group(1)
                        if _ALL_UPPER_NICK_RE.match(nick) and nick not in valid:
                            findings.append(Finding(
                                severity="warn",
                                summary=(
                                    f"unknown owner @{nick} "
                                    "— add to People/ or fix typo"
                                ),
                                location=f"{ou}/{rel}:{lineno}",
                            ))
            except Exception:
                logger.exception("unknown_owner error: %s", abs_path)
    return findings


# ---------------------------------------------------------------------------
# Checker 4 — invalid_dates
# ---------------------------------------------------------------------------

def _is_valid_due(val: str) -> bool:
    val = val.rstrip(",.;:")
    if re.fullmatch(r"[A-Z][a-z]{2}-\d{2}", val):
        return val[:3] in _SHORT_MONTHS and 1 <= int(val[4:]) <= 31
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
        try:
            date.fromisoformat(val)
            return True
        except ValueError:
            return False
    if re.fullmatch(r"Q[1-4]", val):
        return True
    if re.fullmatch(r"\d{4}-Q[1-4]", val):
        return True
    if val in _SHORT_MONTHS:
        return True
    if re.fullmatch(r"\d{4}", val):
        return True
    return False


def _is_valid_start_finish(val: str) -> bool:
    val = val.rstrip(",.;:")
    if re.fullmatch(r"[A-Z][a-z]{2}-\d{4}", val):
        return val[:3] in _SHORT_MONTHS
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
        try:
            date.fromisoformat(val)
            return True
        except ValueError:
            return False
    if re.fullmatch(r"\d{4}-\d{2}", val):
        return 1 <= int(val[5:]) <= 12
    if re.fullmatch(r"\d{4}", val):
        return True
    return False


@register_checker("invalid_dates")
def check_invalid_dates(data_root: str, user_nick: str) -> list[Finding]:
    """Malformed date tokens (due:, start:, finish:) on task lines."""
    findings: list[Finding] = []
    for ou, ou_dir in _find_ous(data_root):
        for abs_path, rel in _all_md_files(ou_dir):
            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
                for lineno, line in enumerate(content.splitlines(), 1):
                    if not _TASK_LINE_RE.match(line):
                        continue
                    for m in _DATE_TOKEN_RE.finditer(line):
                        tok, raw = m.group(1), m.group(2)
                        val = raw.rstrip(",.;:")
                        if not val:
                            continue
                        ok = _is_valid_due(val) if tok == "due" else _is_valid_start_finish(val)
                        if not ok:
                            findings.append(Finding(
                                severity="warn",
                                summary=f"invalid {tok}:'{val}'",
                                location=f"{ou}/{rel}:{lineno}",
                            ))
            except Exception:
                logger.exception("invalid_dates error: %s", abs_path)
    return findings


# ---------------------------------------------------------------------------
# Checker 5 — people_bio_only
# ---------------------------------------------------------------------------

def _people_files(data_root: str) -> list[tuple[Path, str]]:
    """Root People.md plus every <OU>/People/*.md file. Returns (abs_path, display_location) pairs."""
    results: list[tuple[Path, str]] = []
    root_people = Path(data_root) / "People.md"
    if root_people.is_file():
        results.append((root_people, "People.md"))
    for ou, ou_dir in _find_ous(data_root):
        people_dir = ou_dir / "People"
        if not people_dir.is_dir():
            continue
        for f in sorted(people_dir.glob("*.md")):
            results.append((f, f"{ou}/People/{f.name}"))
    return results


@register_checker("people_bio_only")
def check_people_bio_only(data_root: str, user_nick: str) -> list[Finding]:
    """People.md is bio-only (see docs/CORPUS_SCHEMA.md) — a checkbox line is
    drift back into writing plans/action items into a contact's bio."""
    findings: list[Finding] = []
    for abs_path, location in _people_files(data_root):
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(content.splitlines(), 1):
                if _TASK_LINE_RE.match(line):
                    findings.append(Finding(
                        severity="warn",
                        summary="task/action line in a People file — move to inbox.md or the relevant project",
                        location=f"{location}:{lineno}",
                    ))
        except Exception:
            logger.exception("people_bio_only error: %s", abs_path)
    return findings


# ---------------------------------------------------------------------------
# Action — archive_old_daily
# ---------------------------------------------------------------------------

def archive_old_daily(
    data_root: str,
    today: date | None = None,
    *,
    days_threshold: int = 365,
) -> dict:
    """
    Move <OU>/Daily/<YYYY-MM-DD>.md files older than days_threshold days into
    <OU>/Archive/Daily/<YYYY>/<YYYY-MM-DD>.md, then git commit.

    Returns {"moved": N, "by_ou": {ou: count}}.
    """
    if today is None:
        today = date.today()
    cutoff = today - timedelta(days=days_threshold)

    moved_total = 0
    by_ou: dict[str, int] = {}

    for ou, ou_dir in _find_ous(data_root):
        daily_dir = ou_dir / "Daily"
        if not daily_dir.is_dir():
            continue
        count = 0
        for f in sorted(daily_dir.glob("????-??-??.md")):
            try:
                file_date = date.fromisoformat(f.stem)
            except ValueError:
                continue
            if file_date >= cutoff:
                continue
            dst_dir = ou_dir / "Archive" / "Daily" / str(file_date.year)
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / f.name
            try:
                f.rename(dst)
                count += 1
            except OSError:
                logger.warning("archive_old_daily: failed to move %s → %s", f, dst)
        if count:
            by_ou[ou] = count
            moved_total += count

    if moved_total:
        _commit_archive(data_root, moved_total)

    return {"moved": moved_total, "by_ou": by_ou}


def _commit_archive(data_root: str, count: int) -> None:
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
        author = git.Actor("Arivu Baalan", "arivu@smtw.in")
        repo.index.commit(
            f"archive: moved {count} old daily file(s) ({ts})",
            author=author,
            committer=author,
        )
        logger.info("archive_old_daily: committed %d move(s)", count)
    except Exception:
        logger.warning("archive_old_daily: git commit failed (files moved on disk)")


# ---------------------------------------------------------------------------
# Write findings → inbox.md
# ---------------------------------------------------------------------------

def write_findings_to_inbox(data_root: str, findings: list[Finding]) -> int:
    """
    Append new findings to inbox.md under ## Housekeeping.
    Exact-line dedup: already-present lines are skipped.
    Returns count of newly added lines.
    """
    if not findings:
        return 0

    inbox_path = Path(data_root) / "inbox.md"
    if inbox_path.is_file():
        content = inbox_path.read_text(encoding="utf-8", errors="replace")
    else:
        content = "# Inbox\n\n"

    existing = set(content.splitlines())
    new_lines: list[str] = []
    for f in findings:
        loc = f.location or ""
        line = f"- [ ] 🔎{loc} — {f.summary}" if loc else f"- [ ] 🔎 {f.summary}"
        if line not in existing:
            new_lines.append(line)

    if not new_lines:
        return 0

    block = "\n".join(new_lines) + "\n"

    if "## Housekeeping" in content:
        idx = content.find("## Housekeeping")
        nxt = content.find("\n## ", idx + 1)
        if nxt == -1:
            if not content.endswith("\n"):
                content += "\n"
            content += block
        else:
            content = content[:nxt] + block + content[nxt:]
    else:
        anchor = content.find("\n## AI Notes")
        section = f"\n\n## Housekeeping\n\n{block}"
        if anchor == -1:
            if not content.endswith("\n"):
                content += "\n"
            content += section
        else:
            content = content[:anchor] + section + content[anchor:]

    inbox_path.write_text(content, encoding="utf-8")
    return len(new_lines)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

def run_housekeeping(data_root: str, user_nick: str = "ADMIN") -> dict:
    """
    Run all registered checkers, write findings to inbox.md, archive old daily files.
    Per-checker errors are caught so one bad checker doesn't block others.
    """
    today = date.today()
    logger.info(
        "run_housekeeping start: data_root=%s user_nick=%s date=%s",
        data_root, user_nick, today,
    )

    checker_results: dict[str, int] = {}
    all_findings: list[Finding] = []

    for name, fn in _CHECKERS:
        try:
            found = fn(data_root, user_nick)
            checker_results[name] = len(found)
            all_findings.extend(found)
            logger.info("checker %s: %d finding(s)", name, len(found))
        except Exception:
            logger.exception("checker %s raised", name)
            checker_results[name] = 0

    try:
        added = write_findings_to_inbox(data_root, all_findings)
    except Exception:
        logger.exception("write_findings_to_inbox failed")
        added = 0

    try:
        archive_result = archive_old_daily(data_root, today)
    except Exception:
        logger.exception("archive_old_daily failed")
        archive_result = {"moved": 0, "by_ou": {}}

    result = {
        "date": str(today),
        "checkers": checker_results,
        "findings_total": len(all_findings),
        "findings_added_to_inbox": added,
        "archive": archive_result,
    }
    logger.info("run_housekeeping done: %s", result)
    return result
