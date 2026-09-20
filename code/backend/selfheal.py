"""
Self-check and self-heal — detect known failure modes and repair the ones that
are safe to repair without a human.

The system already retries individual tasks and restarts crashed containers.
What it could not do was notice that it had ended up in a *stuck but running*
state: a queue job that exhausted its retries and stopped forever, an
abandoned git lock making the whole corpus read-only, a vector index that
silently holds nothing, today's Daily file never materialised, a disk quietly
filling up. None of those crash anything. They just make the assistant wrong,
and until now only a person noticing could fix them.

This runs every 15 minutes and produces a list of Findings. A finding is one
of:

    ok     — checked, nothing wrong
    fixed  — something was wrong and has been repaired automatically
    alert  — something is wrong that a human has to decide about

Everything at `alert` is routed through alerts.notify(), which throttles per
key, so a condition that persists for a week is one email per cooldown window
rather than 672 of them.

The line between "fixed" and "alert" is deliberate: this repairs things that
are idempotent and reversible (re-run a job, clear an abandoned lock file,
rebuild a derived index) and never touches anything that could destroy data or
duplicate a side effect. A corrupt database is reported, never "repaired".

Public API:
    run_checks(conn, data_root=None) -> list[Finding]
"""

import logging
import os
import shutil
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime

import alerts
import config
import system_stats
import task_queue

logger = logging.getLogger(__name__)

OK = "ok"
FIXED = "fixed"
ALERT = "alert"

# Task types the self-check may requeue on its own after they failed
# permanently. Every one of these is idempotent and has no external side
# effect, so running it a second time is at worst wasted work.
#
# Deliberately absent:
#   email             — would re-send a message that may already have gone out
#   morning_briefing  — sends mail; a failure after the send would duplicate it
#   news_watch_*      — submits paid Batches API work and advances dedup state
_AUTO_RECOVER_TASK_TYPES = frozenset({
    "materialise",
    "md_reindex",
    "index_sync",
    "commit_pending",
    "housekeeping",
    "db_backup",
    "offsite_push",
})

# A health probe gets a few tries before it is believed — one refused
# connection during a container restart is not an outage.
_HEALTH_ATTEMPTS = 3
_HEALTH_TIMEOUT_SEC = 5


@dataclass
class Finding:
    check: str
    status: str
    detail: str
    alert_key: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"check": self.check, "status": self.status, "detail": self.detail, **self.extra}


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_stale_locks(conn, data_root: str) -> Finding:
    """
    Abandoned git locks make the entire corpus read-only, and .git/index.lock
    is cleared by nothing else in the system.
    """
    import md_editor

    removed = md_editor.clear_stale_git_locks(data_root)
    if not removed:
        return Finding("stale_locks", OK, "no abandoned locks")
    return Finding(
        "stale_locks", FIXED,
        f"removed {len(removed)} abandoned lock(s): {', '.join(removed)}",
        extra={"removed": removed},
    )


def check_failed_tasks(conn, data_root: str) -> Finding:
    """
    Give permanently-failed idempotent jobs exactly one more chance.

    One retry, not a loop: recovered_at is stamped on the way through, and a
    task that fails again after being recovered is left alone for a human. The
    alert for the original failure has already gone out via the worker.
    """
    rows = conn.execute(
        """
        SELECT id, task_type, last_error FROM task_queue
        WHERE status = 'failed' AND recovered_at IS NULL
        ORDER BY id
        """
    ).fetchall()
    if not rows:
        return Finding("failed_tasks", OK, "no failed tasks awaiting recovery")

    requeued, skipped = [], []
    for row in rows:
        if row["task_type"] not in _AUTO_RECOVER_TASK_TYPES:
            skipped.append(f"{row['id']}:{row['task_type']}")
            continue
        conn.execute(
            """
            UPDATE task_queue
            SET status = 'pending', attempts = 0, locked_until = NULL,
                scheduled_for = datetime('now', 'localtime'),
                recovered_at  = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (row["id"],),
        )
        requeued.append(f"{row['id']}:{row['task_type']}")

    if not requeued:
        return Finding(
            "failed_tasks", ALERT,
            f"{len(skipped)} failed task(s) need a human (not safe to auto-retry): "
            + ", ".join(skipped),
            alert_key="failed_tasks:manual",
            extra={"skipped": skipped},
        )
    return Finding(
        "failed_tasks", FIXED,
        f"requeued {len(requeued)} failed task(s): " + ", ".join(requeued),
        extra={"requeued": requeued, "skipped": skipped},
    )


def check_todays_daily(conn, data_root: str) -> Finding:
    """
    If today has no Daily file in any OU, the materialiser did not run (or ran
    and failed). Today's view is empty and the briefing has nothing to work
    from. Re-running it is idempotent, so just enqueue it.
    """
    today = date.today().isoformat()
    ous = _find_ous(data_root)
    if not ous:
        return Finding("todays_daily", OK, "no OUs in corpus yet")

    have = [ou for ou in ous if os.path.isfile(os.path.join(data_root, ou, "Daily", f"{today}.md"))]
    if have:
        return Finding("todays_daily", OK, f"{len(have)}/{len(ous)} OU(s) have today's Daily file")

    if _already_queued(conn, "materialise"):
        return Finding("todays_daily", OK, "no Daily file yet, but materialise is already queued")

    task_queue.enqueue(conn, "materialise", {}, commit=False)
    return Finding(
        "todays_daily", FIXED,
        f"no Daily file for {today} in any OU — enqueued materialise",
    )


def check_vector_index(conn, data_root: str) -> Finding:
    """
    Chroma holding nothing while the corpus holds files means RAG answers are
    silently running blind. The index is derived data, so rebuilding it is
    always safe — that is the whole reason it is listed as rebuildable.
    """
    import md_indexer

    md_files = md_indexer._walk_md_files(data_root)
    if not md_files:
        return Finding("vector_index", OK, "corpus has no MD files to index")

    try:
        count = md_indexer._get_collection().count()
    except Exception as exc:
        return Finding(
            "vector_index", ALERT,
            f"ChromaDB unreachable ({exc}) — RAG answers are running without corpus context",
            alert_key="vector_index:unreachable",
        )

    if count > 0:
        return Finding("vector_index", OK, f"{count} chunk(s) indexed")

    if _already_queued(conn, "md_reindex"):
        return Finding("vector_index", OK, "index empty but md_reindex is already queued")

    task_queue.enqueue(conn, "md_reindex", {"full": True}, commit=False)
    return Finding(
        "vector_index", FIXED,
        f"index empty while corpus has {len(md_files)} file(s) — enqueued md_reindex",
    )


def check_disk_space(conn, data_root: str) -> Finding:
    """
    The box runs on a 10GB volume that is regularly past 80%. A full disk
    fails SQLite writes and git commits at the same time, which looks like
    everything breaking at once.
    """
    try:
        usage = shutil.disk_usage(data_root if os.path.isdir(data_root) else os.getcwd())
    except OSError as exc:
        return Finding("disk_space", ALERT, f"could not read disk usage: {exc}",
                       alert_key="disk_space:unreadable")

    used_pct = usage.used / usage.total * 100
    free_gb = usage.free / (1024 ** 3)
    detail = f"{used_pct:.0f}% used, {free_gb:.1f} GB free"

    if used_pct >= config.DISK_ALERT_PERCENT:
        return Finding(
            "disk_space", ALERT,
            f"disk {detail} — past the {config.DISK_ALERT_PERCENT}% threshold. "
            "Check Admin > Server for the breakdown and trend. The usual culprit "
            "is the Docker build cache (`docker builder prune -f`), which "
            "`docker image prune -af` does NOT touch; then db/backups/.",
            alert_key="disk_space:low",
            extra={"used_pct": round(used_pct, 1), "free_gb": round(free_gb, 2)},
        )
    return Finding("disk_space", OK, detail,
                   extra={"used_pct": round(used_pct, 1), "free_gb": round(free_gb, 2)})


def check_db_integrity(conn, data_root: str) -> Finding:
    """
    Reported, never repaired. A corrupt SQLite file is a restore-from-backup
    decision, not something a background job should improvise.
    """
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.Error as exc:
        return Finding("db_integrity", ALERT, f"quick_check failed to run: {exc}",
                       alert_key="db_integrity:error")

    if str(result).lower() == "ok":
        return Finding("db_integrity", OK, "quick_check ok")
    return Finding(
        "db_integrity", ALERT,
        f"SQLite quick_check reported: {result}. Restore from db/backups/ — "
        "see docs/RECOVERY.md. Not repairing automatically.",
        alert_key="db_integrity:corrupt",
    )


def check_backend_health(conn, data_root: str) -> Finding:
    """
    The worker watching the web process. Compose restarts a container that
    *exits*, but a backend that is up and wedged stays up and wedged — the
    healthcheck only gates startup ordering, nothing acts on it later.

    This cannot restart it (that would need the Docker socket mounted into the
    app, which hands a web process root on the host). It reports, loudly.
    """
    url = config.HEALTH_CHECK_URL
    if not url:
        return Finding("backend_health", OK, "health check URL not configured")

    last_error = ""
    for attempt in range(_HEALTH_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=_HEALTH_TIMEOUT_SEC) as resp:
                if 200 <= resp.status < 300:
                    return Finding("backend_health", OK, f"{url} ok")
                last_error = f"HTTP {resp.status}"
        except (urllib.error.URLError, OSError) as exc:
            last_error = str(exc)

    return Finding(
        "backend_health", ALERT,
        f"backend health check failed {_HEALTH_ATTEMPTS}x at {url}: {last_error}. "
        "The worker is alive, so this is the web process alone — "
        "`docker compose restart backend` on the box.",
        alert_key="backend_health:down",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_ous(data_root: str) -> list[str]:
    """
    Directories directly under the data root that are OUs.

    Delegates to the materialiser's own discovery so both agree on what counts
    — this check exists to notice when the materialiser did not run, so
    disagreeing with it about the set of OUs would be its own bug. (An earlier
    local copy of this rule counted `logs/` as an OU.) No fixed taxonomy: OU
    names are whatever the user made, see CLAUDE.md.
    """
    import materialiser
    return materialiser._find_ous(data_root)


def _already_queued(conn, task_type: str) -> bool:
    """Don't pile up duplicates of a job that is already waiting to run."""
    row = conn.execute(
        """
        SELECT 1 FROM task_queue
        WHERE task_type = ? AND status IN ('pending', 'running') LIMIT 1
        """,
        (task_type,),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_CHECKS = (
    check_stale_locks,
    check_failed_tasks,
    check_todays_daily,
    check_vector_index,
    check_disk_space,
    check_db_integrity,
    check_backend_health,
)


def run_checks(conn, data_root: str | None = None) -> list[Finding]:
    """
    Run every check, raise an alert for each finding that needs a human, and
    return all findings.

    Each check is isolated: one blowing up must not stop the rest, or a single
    broken probe would take the whole safety net down with it.
    """
    root = data_root or config.USER_DATA_ROOT
    findings: list[Finding] = []

    for check in _CHECKS:
        try:
            findings.append(check(conn, root))
        except Exception as exc:
            logger.exception("selfheal: check %s blew up", check.__name__)
            findings.append(Finding(
                check.__name__, ALERT, f"check itself failed: {exc}",
                alert_key=f"selfcheck_broken:{check.__name__}",
            ))

    # Record one usage sample per run, regardless of how the checks went.
    # Isolated like a check is: a metrics table is a nice-to-have, and it must
    # never be the reason the safety net stops running. Not committed here —
    # the worker owns the transaction.
    try:
        system_stats.record_sample(conn, root)
    except Exception:
        logger.exception("selfheal: could not record system metrics sample")

    for finding in findings:
        if finding.status == ALERT:
            alerts.notify(
                conn,
                finding.alert_key or f"selfcheck:{finding.check}",
                f"[SLEEPY] Self-check: {finding.check}",
                finding.detail,
            )

    fixed = [f for f in findings if f.status == FIXED]
    alerted = [f for f in findings if f.status == ALERT]
    logger.info(
        "selfheal: %d check(s), %d fixed, %d needing attention",
        len(findings), len(fixed), len(alerted),
    )
    for f in fixed + alerted:
        logger.warning("selfheal: %s [%s] %s", f.check, f.status, f.detail)

    return findings
