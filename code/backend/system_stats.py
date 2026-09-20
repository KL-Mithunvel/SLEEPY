"""
Host usage + space reporting for the Admin > Server tab.

Answers "how is the box doing, and what is eating the disk" from inside the
backend container, without a Docker socket. Mounting the socket into a web
process would hand any request-path bug root on the host, and the same call
was already declined for container restarts in selfheal.check_backend_health
— this module keeps that line.

That has one consequence worth stating plainly: the largest consumer on this
box is Docker images (~2.8GB of a 10GB volume), and it is *invisible* from in
here. Only the total is visible, via a bind-mounted path, so a reading of
"76% used" cannot be broken down into images-vs-corpus by this process alone.
`tooling/deploy-prod.sh` closes that gap by writing `docker system df` output
to DEPLOY_SNAPSHOT_PATH on the host at deploy time; read_deploy_snapshot()
picks it up and the tab labels it with its own timestamp, so a stale snapshot
reads as stale rather than as current truth.

Public API:
    collect(data_root)            -- live snapshot: disk, memory, load, per-area sizes
    record_sample(conn, root)     -- insert one system_metrics row (does NOT commit)
    read_trend(conn, days=14)     -- daily min/max/avg series for the chart
    read_deploy_snapshot()        -- docker breakdown as of last deploy, or None
    summarise(current, trend, deploy) -- plain-English reading of the above
"""

import json
import logging
import os
import shutil
import sqlite3

import config

logger = logging.getLogger(__name__)

# Written by tooling/deploy-prod.sh on the host, read here. Lives under db/
# (derived state, gitignored in the corpus repo) so it never lands in MD
# corpus git history the way anything at the corpus root would.
DEPLOY_SNAPSHOT_NAME = "deploy-snapshot.json"

_GB = 1024 ** 3


# ---------------------------------------------------------------------------
# Directory sizing
# ---------------------------------------------------------------------------

def _dir_bytes(path: str) -> int | None:
    """
    Recursive size of `path`, or None if it is missing/unreadable.

    None and 0 mean different things here and the tab shows them differently:
    0 is "this exists and is empty", None is "this process cannot see it"
    (the Chroma volume in prod belongs to another container, for instance).
    Individual unreadable entries are skipped rather than aborting the walk,
    so one bad file cannot blank out the whole figure.
    """
    if not path or not os.path.isdir(path):
        return None

    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                st = os.stat(os.path.join(root, name))
            except OSError:
                continue
            # st_blocks would be truer to on-disk cost, but it is absent on
            # Windows, and dev/prod reporting different numbers for the same
            # corpus is worse than both being slightly optimistic.
            total += st.st_size
    return total


def _is_inside(child: str, parent: str) -> bool:
    """
    True if `child` sits under `parent`. Used to decide whether a directory's
    bytes were already counted by the corpus walk, so they get subtracted
    exactly once and only when they were double-counted in the first place.
    """
    if not child or not parent:
        return False
    try:
        return os.path.commonpath([
            os.path.realpath(child), os.path.realpath(parent)
        ]) == os.path.realpath(parent)
    except (OSError, ValueError):   # ValueError: different drives on Windows
        return False


def _db_paths() -> tuple[str, str]:
    """Absolute (sqlite dir, backups dir). Mirrors offsite.py's layout assumption."""
    raw = config.SQLITE_DB_PATH
    if raw == ":memory:":
        return "", ""
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    db_file = raw if os.path.isabs(raw) else os.path.abspath(os.path.join(backend_dir, raw))
    sqlite_dir = os.path.dirname(db_file)                       # .../db/sqlite
    backups_dir = os.path.join(os.path.dirname(sqlite_dir), "backups")
    return sqlite_dir, backups_dir


# ---------------------------------------------------------------------------
# Host readings
# ---------------------------------------------------------------------------

def _memory() -> dict:
    """
    Host memory from /proc/meminfo. Absent on Windows dev, so every field is
    optional and the tab hides the card rather than showing zeroes.
    """
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            raw = {
                parts[0].rstrip(":"): int(parts[1]) * 1024
                for line in fh
                if len(parts := line.split()) >= 2 and parts[1].isdigit()
            }
    except (OSError, ValueError):
        return {"total_bytes": None, "available_bytes": None, "used_pct": None}

    total = raw.get("MemTotal")
    # MemAvailable, not MemFree: free excludes reclaimable page cache, which on
    # a 913MB t3.micro makes a healthy box look permanently out of memory.
    available = raw.get("MemAvailable")
    used_pct = round((total - available) / total * 100, 1) if total and available is not None else None
    return {"total_bytes": total, "available_bytes": available, "used_pct": used_pct}


def _uptime_seconds() -> float | None:
    try:
        with open("/proc/uptime", encoding="utf-8") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _load_average() -> list[float] | None:
    try:
        return [round(v, 2) for v in os.getloadavg()]
    except (OSError, AttributeError):   # AttributeError: Windows
        return None


def collect(data_root: str | None = None) -> dict:
    """
    One live reading. Never raises — a monitoring view that 500s when the
    thing it monitors is unhealthy is worse than useless, so every field
    degrades to None independently.
    """
    root = data_root or config.USER_DATA_ROOT
    sqlite_dir, backups_dir = _db_paths()

    # statvfs on a bind-mounted path reports the HOST volume, which is the
    # number that matters — the container's own overlay figure is not what
    # fills up.
    try:
        usage = shutil.disk_usage(root if os.path.isdir(root) else os.getcwd())
        disk = {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "used_pct": round(usage.used / usage.total * 100, 1) if usage.total else None,
            "alert_pct": config.DISK_ALERT_PERCENT,
        }
    except OSError as exc:
        logger.warning("system_stats: disk usage unreadable: %s", exc)
        disk = {"total_bytes": None, "used_bytes": None, "free_bytes": None,
                "used_pct": None, "alert_pct": config.DISK_ALERT_PERCENT}

    corpus = _dir_bytes(root)
    db_bytes = _dir_bytes(sqlite_dir)
    backups = _dir_bytes(backups_dir)
    chroma = _dir_bytes(config.CHROMA_PATH)

    # The corpus walk counts db/ too, so strip the derived parts back out —
    # otherwise SQLite and its backups are double-counted and "documents"
    # looks several hundred MB too big.
    #
    # Subtract ONLY the ones actually inside `root`, though. CHROMA_PATH in
    # particular is independently configurable and in prod belongs to another
    # container entirely; subtracting a directory the walk never visited
    # drove corpus_bytes to 0 on the dev box, where CHROMA_PATH points at a
    # populated folder outside the measured root.
    if corpus is not None:
        for path, size in ((sqlite_dir, db_bytes),
                           (backups_dir, backups),
                           (config.CHROMA_PATH, chroma)):
            if size and _is_inside(path, root):
                corpus -= size
        corpus = max(0, corpus)

    return {
        "disk": disk,
        "memory": _memory(),
        "load_average": _load_average(),
        "uptime_seconds": _uptime_seconds(),
        "areas": {
            "corpus_bytes": corpus,
            "db_bytes": db_bytes,
            "backups_bytes": backups,
            "chroma_bytes": chroma,
        },
    }


# ---------------------------------------------------------------------------
# Sampling + trend
# ---------------------------------------------------------------------------

def record_sample(conn: sqlite3.Connection, data_root: str | None = None) -> dict:
    """
    Record one reading. Called from run_checks every 15 min.

    Does NOT commit — the worker owns the transaction boundary.
    """
    snap = collect(data_root)
    conn.execute(
        """
        INSERT INTO system_metrics
            (disk_total_bytes, disk_used_bytes, disk_free_bytes, disk_used_pct,
             mem_total_bytes, mem_available_bytes,
             corpus_bytes, db_bytes, backups_bytes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snap["disk"]["total_bytes"], snap["disk"]["used_bytes"],
            snap["disk"]["free_bytes"], snap["disk"]["used_pct"],
            snap["memory"]["total_bytes"], snap["memory"]["available_bytes"],
            snap["areas"]["corpus_bytes"], snap["areas"]["db_bytes"],
            snap["areas"]["backups_bytes"],
        ),
    )
    return snap


def read_trend(conn: sqlite3.Connection, days: int = 14) -> list[dict]:
    """
    Daily disk figures, oldest first. Grouped by day rather than returned raw
    because 96 samples a day would make the chart unreadable without saying
    anything the daily max does not already say.
    """
    rows = conn.execute(
        """
        SELECT substr(created_at, 1, 10)      AS day,
               COUNT(*)                        AS samples,
               ROUND(MIN(disk_used_pct), 1)    AS min_pct,
               ROUND(MAX(disk_used_pct), 1)    AS max_pct,
               ROUND(AVG(disk_used_pct), 1)    AS avg_pct,
               MIN(disk_free_bytes)            AS min_free_bytes
        FROM system_metrics
        WHERE disk_used_pct IS NOT NULL
        GROUP BY day
        ORDER BY day DESC
        LIMIT ?
        """,
        (max(1, min(days, 90)),),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ---------------------------------------------------------------------------
# Deploy-time Docker snapshot
# ---------------------------------------------------------------------------

_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


def parse_docker_size(text) -> int | None:
    """
    "4.55GB" / "101.6MB" / "0B" -> bytes.

    Docker prints human-readable sizes and offers no bytes format for
    `system df`, so the deploy script stores exactly what Docker printed and
    the normalising happens here, where it is testable. Anything unparseable
    returns None rather than a misleading 0.
    """
    if not isinstance(text, str):
        return None
    raw = text.strip().split()[0] if text.strip() else ""
    for unit in ("TB", "GB", "MB", "KB", "B"):
        if raw.upper().endswith(unit):
            try:
                return int(float(raw[: -len(unit)]) * _SIZE_UNITS[unit])
            except ValueError:
                return None
    return None


def read_deploy_snapshot(data_root: str | None = None) -> dict | None:
    """
    The `docker system df` reading captured by deploy-prod.sh, or None if no
    deploy has written one yet (always the case in dev).

    Treated as untrusted input: written by a shell script outside this
    process, so anything malformed degrades to None rather than breaking the
    page. Returns the raw rows plus normalised byte totals for the two that
    matter — images and build cache.
    """
    root = data_root or config.USER_DATA_ROOT
    path = os.path.join(root, "db", DEPLOY_SNAPSHOT_NAME)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    rows = data.get("docker")
    rows = rows if isinstance(rows, list) else []
    by_type = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("Type"), str):
            by_type[row["Type"].strip().lower()] = row

    def size_of(type_name: str, key: str = "Size") -> int | None:
        row = by_type.get(type_name)
        return parse_docker_size(row.get(key)) if row else None

    data["images_bytes"] = size_of("images")
    data["images_reclaimable_bytes"] = size_of("images", "Reclaimable")
    data["build_cache_bytes"] = size_of("build cache")
    data["containers_bytes"] = size_of("containers")
    data["volumes_bytes"] = size_of("local volumes")
    return data


# ---------------------------------------------------------------------------
# Plain-English reading
# ---------------------------------------------------------------------------

def _human(n) -> str:
    """
    Pick a unit that carries information. Reporting a 1MB database as "0.0 GB"
    is technically true and tells the reader nothing.
    """
    if n is None:
        return "unknown"
    if n < 1024:
        return f"{n} B"
    value = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if value >= 100 else f"{value:.1f} {unit}"
    return f"{value:.1f} TB"


def summarise(current: dict, trend: list[dict], deploy: dict | None) -> list[str]:
    """
    Turn the numbers into sentences. Deterministic, not LLM-generated: this
    has to be right when the box is sick, cost nothing, and never wait on an
    API call.
    """
    lines: list[str] = []
    disk = current.get("disk", {})
    pct, free = disk.get("used_pct"), disk.get("free_bytes")
    alert_at = disk.get("alert_pct")

    if pct is None:
        lines.append("Disk usage could not be read on this host.")
    else:
        head = f"Disk is {pct:.0f}% full, {_human(free)} free of {_human(disk.get('total_bytes'))}."
        if alert_at and pct >= alert_at:
            head += f" That is past the {alert_at}% alert threshold — reclaim space now."
        elif alert_at:
            head += f" The alert threshold is {alert_at}%."
        lines.append(head)

    # Growth rate is the part a single reading cannot tell you, and the part
    # that actually predicts running out.
    if len(trend) >= 2 and trend[0].get("max_pct") is not None and trend[-1].get("max_pct") is not None:
        first, last = trend[0], trend[-1]
        delta = last["max_pct"] - first["max_pct"]
        span = len(trend)
        if abs(delta) < 0.5:
            lines.append(f"It has held steady over the last {span} day(s) of samples.")
        else:
            direction = "grown" if delta > 0 else "dropped"
            lines.append(
                f"It has {direction} {abs(delta):.0f} percentage points over the last "
                f"{span} day(s), from {first['max_pct']:.0f}% to {last['max_pct']:.0f}%."
            )
            if delta > 0 and free is not None:
                per_day = delta / max(1, span - 1)
                if per_day > 0.1 and pct is not None:
                    days_left = (100 - pct) / per_day
                    if days_left < 120:
                        lines.append(
                            f"At that rate it reaches 100% in roughly {days_left:.0f} day(s)."
                        )
    else:
        lines.append("Not enough samples yet to show a trend — check back after a day or so.")

    areas = current.get("areas", {})
    named = sum(v for v in areas.values() if isinstance(v, int))
    used = disk.get("used_bytes")
    if used and named:
        # Name every component that went into the total, or the arithmetic
        # looks wrong to anyone who tries to add the parts up.
        parts = ", ".join(
            f"{label} {_human(areas.get(key))}"
            for label, key in (
                ("documents", "corpus_bytes"),
                ("database", "db_bytes"),
                ("backups", "backups_bytes"),
                ("vector index", "chroma_bytes"),
            )
            if isinstance(areas.get(key), int)
        )
        lines.append(f"Of that, this app's own data accounts for {_human(named)} ({parts}).")
        rest = used - named
        if rest > 0:
            lines.append(
                f"The remaining {_human(rest)} is the operating system and Docker, which this "
                "process cannot itemise from inside its container."
            )

    if deploy:
        cache = deploy.get("build_cache_bytes")
        when = deploy.get("captured_at", "an earlier deploy")
        if isinstance(cache, int) and cache > 2 * _GB:
            lines.append(
                f"As of {when}, Docker build cache was {_human(cache)} — that is the thing that "
                "filled this disk before. Run `docker builder prune -f`."
            )
        elif deploy.get("images_bytes"):
            lines.append(
                f"As of {when}, Docker images were {_human(deploy.get('images_bytes'))} "
                f"and build cache {_human(cache)}."
            )

    mem = current.get("memory", {})
    if mem.get("used_pct") is not None:
        lines.append(
            f"Memory is {mem['used_pct']:.0f}% used, {_human(mem.get('available_bytes'))} available."
        )

    return lines
