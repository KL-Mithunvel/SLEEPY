"""
Retention for the two append-only operational tables, system_alerts and
system_metrics: archive first, offsite second, delete last.

Both tables only ever grow. They are small (the whole DB is a few MB), so this
is hygiene rather than rescue — but one of them proved the point: a single
unretryable failed task re-alerted every 15 minutes for 13 days and left 1,277
near-identical rows in system_alerts. Raw rows are useful for a few weeks and
noise after that; what is worth keeping long-term is the shape ("this alert
fired 96 times a day for two weeks"), which fits in a few lines.

Order matters, and it is the whole point of this module:

    1. roll old rows up to one JSON entry per day (per alert_key for alerts)
    2. commit the JSON into the corpus repo
    3. push the corpus repo offsite
    4. only if that push really happened, DELETE the raw rows

A rollup that has not reached the remote is not a backup, so anything short of
a confirmed push (no remote configured, push disabled, push raised) leaves
every row in place and the next nightly run tries again. Nothing is ever lost
to a half-finished run.

Idempotent by construction. Only whole days strictly older than the cutoff are
eligible, and each day's entry in the monthly file is *replaced*, never added
to, so re-running after a crash between steps cannot double-count.

Files live under `archive/ops/` as JSON: root `archive/` is already skipped by
every OU-discovery routine (materialiser, housekeeping, task_scan, logs_bp,
goal_planner), so no module mistakes it for a life-domain folder, and the
indexer only reads .md, so ops numbers never surface in the AI's search of the
user's notes.

Public API:
    run_retention(conn, data_root=None, today=None) -> dict
"""

import json
import logging
import os
import sqlite3
from datetime import date, timedelta

import config

logger = logging.getLogger(__name__)

ARCHIVE_SUBDIR = os.path.join("archive", "ops")


def _cutoff(today: date, days: int) -> str:
    """
    ISO date string; rows with created_at < this are on a strictly earlier day.
    ("2026-09-03 10:00:00" > "2026-09-03" lexically, so the cutoff day itself is
    never eligible and a day is only ever archived once it is complete.)
    """
    return (today - timedelta(days=max(1, days))).isoformat()


# ---------------------------------------------------------------------------
# 1. Roll up
# ---------------------------------------------------------------------------

def _rollup_alerts(conn: sqlite3.Connection, cutoff: str) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT substr(created_at, 1, 10) AS day, alert_key,
               COUNT(*)        AS count,
               SUM(emailed)    AS emailed,
               SUM(suppressed) AS suppressed,
               MIN(created_at) AS first_seen,
               MAX(created_at) AS last_seen,
               MAX(subject)    AS subject
        FROM system_alerts
        WHERE created_at < ?
        GROUP BY day, alert_key
        ORDER BY day, alert_key
        """,
        (cutoff,),
    ).fetchall()
    days: dict[str, dict] = {}
    for r in rows:
        days.setdefault(r["day"], {})[r["alert_key"]] = {
            "count": r["count"],
            "emailed": r["emailed"] or 0,
            "suppressed": r["suppressed"] or 0,
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
            "subject": r["subject"],
        }
    return days


def _rollup_metrics(conn: sqlite3.Connection, cutoff: str) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT substr(created_at, 1, 10)  AS day,
               COUNT(*)                   AS samples,
               MIN(disk_used_pct)         AS disk_used_pct_min,
               MAX(disk_used_pct)         AS disk_used_pct_max,
               MIN(disk_free_bytes)       AS disk_free_bytes_min,
               MIN(mem_available_bytes)   AS mem_available_bytes_min,
               MAX(corpus_bytes)          AS corpus_bytes_max,
               MAX(db_bytes)              AS db_bytes_max,
               MAX(backups_bytes)         AS backups_bytes_max
        FROM system_metrics
        WHERE created_at < ?
        GROUP BY day
        ORDER BY day
        """,
        (cutoff,),
    ).fetchall()
    return {r["day"]: {k: r[k] for k in r.keys() if k != "day"} for r in rows}


# ---------------------------------------------------------------------------
# 2. Write the monthly files
# ---------------------------------------------------------------------------

def _write_months(archive_dir: str, kind: str, days: dict[str, dict]) -> list[str]:
    """
    Merge rolled-up days into `<kind>-YYYY-MM.json`, replacing each day's entry.
    Returns the absolute paths written.
    """
    by_month: dict[str, dict[str, dict]] = {}
    for day, entry in days.items():
        by_month.setdefault(day[:7], {})[day] = entry

    written = []
    for month, month_days in sorted(by_month.items()):
        path = os.path.join(archive_dir, f"{kind}-{month}.json")
        data = {"kind": kind, "month": month, "days": {}}
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing.get("days"), dict):
                    data["days"] = existing["days"]
            except (OSError, ValueError):
                # An unreadable file is rebuilt from the rows we still hold;
                # nothing has been deleted yet, so nothing is lost by that.
                logger.warning("retention: could not read %s, rebuilding it", path)
        data["days"].update(month_days)
        data["days"] = dict(sorted(data["days"].items()))

        os.makedirs(archive_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        written.append(path)
    return written


# ---------------------------------------------------------------------------
# 3. Commit
# ---------------------------------------------------------------------------

def _commit(data_root: str, paths: list[str], message: str) -> bool:
    """Commit just these files. Returns False when there is no corpus repo."""
    import git
    import md_editor

    try:
        repo = git.Repo(data_root, search_parent_directories=False)
    except git.InvalidGitRepositoryError:
        return False

    rel = [os.path.relpath(p, data_root).replace(os.sep, "/") for p in paths]
    with md_editor.corpus_git_lock(data_root):
        md_editor.ensure_corpus_gitignore(data_root)
        if not repo.git.status("--porcelain", "--", *rel).strip():
            return True        # identical to what is already committed
        repo.git.add("--", *rel)
        author = md_editor.ai_actor()
        repo.index.commit(message, author=author, committer=author)
    return True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_retention(conn: sqlite3.Connection, data_root: str | None = None,
                  today: date | None = None) -> dict:
    """
    Archive, push, then delete. Does not commit the DB transaction — the
    worker owns that. "Could not push yet" is not an error: it is reported in
    the result and leaves every row alone. A push that is explicitly enabled
    and fails does raise (offsite.push_corpus), so the task retries and alerts.
    """
    root = data_root or config.USER_DATA_ROOT
    today = today or date.today()
    archive_dir = os.path.join(root, ARCHIVE_SUBDIR)

    alert_cut = _cutoff(today, config.ALERT_RETENTION_DAYS)
    metric_cut = _cutoff(today, config.METRICS_RETENTION_DAYS)

    alert_days = _rollup_alerts(conn, alert_cut)
    metric_days = _rollup_metrics(conn, metric_cut)

    result = {
        "alert_days": len(alert_days), "metric_days": len(metric_days),
        "archived": False, "pushed": False,
        "deleted_alerts": 0, "deleted_metrics": 0, "reason": "nothing to archive",
    }
    if not alert_days and not metric_days:
        return result

    paths = (_write_months(archive_dir, "alerts", alert_days)
             + _write_months(archive_dir, "metrics", metric_days))
    result["archived"] = True

    newest = max(list(alert_days) + list(metric_days))
    if not _commit(root, paths, f"ops: archive alert/metric rollups through {newest}"):
        result["reason"] = f"no git repo at {root}, rows kept"
        logger.warning("retention: %s", result["reason"])
        return result

    import offsite
    push = offsite.push_corpus(root)
    result["pushed"] = bool(push.get("pushed"))
    if not result["pushed"]:
        result["reason"] = f"not pushed offsite ({push.get('reason')}), rows kept"
        logger.warning("retention: %s", result["reason"])
        return result

    result["deleted_alerts"] = conn.execute(
        "DELETE FROM system_alerts WHERE created_at < ?", (alert_cut,)).rowcount
    result["deleted_metrics"] = conn.execute(
        "DELETE FROM system_metrics WHERE created_at < ?", (metric_cut,)).rowcount
    result["reason"] = "ok"
    logger.info("retention: %s", result)
    return result
