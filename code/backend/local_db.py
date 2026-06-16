"""
SQLite connection management and migration engine.
- Single writer convention: backend owns schema, worker never migrates.
- init_db() is called once at startup; applies any pending migrations.
- get_db() / return_db() are used per-request via Flask g.
"""

import logging
import os
import sqlite3
import threading

import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_db_path: str | None = None


# ---------------------------------------------------------------------------
# Migrations — append only, never edit applied entries
# ---------------------------------------------------------------------------

_MIGRATIONS = [
    (1, "Initial schema", [
        """
        CREATE TABLE IF NOT EXISTS db_version (
            version     INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type      TEXT NOT NULL,
            prompt_hash     TEXT,
            model           TEXT,
            diff            TEXT,
            accepted        INTEGER,   -- 1 = accepted, 0 = rejected, NULL = pending
            voided          INTEGER NOT NULL DEFAULT 0,
            latency_ms      INTEGER,
            input_tokens    INTEGER,
            output_tokens   INTEGER,
            created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS task_queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type       TEXT NOT NULL,
            payload         TEXT,      -- JSON
            status          TEXT NOT NULL DEFAULT 'pending',
            attempts        INTEGER NOT NULL DEFAULT 0,
            max_attempts    INTEGER NOT NULL DEFAULT 3,
            scheduled_for   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            locked_until    TEXT,
            last_error      TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            completed_at    TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_task_queue_pending ON task_queue (status, scheduled_for) WHERE status = 'pending'",
        """
        CREATE TABLE IF NOT EXISTS md_chunks_meta (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path       TEXT NOT NULL,
            chunk_index     INTEGER NOT NULL,
            heading         TEXT,
            file_hash       TEXT,
            indexed_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            UNIQUE(file_path, chunk_index)
        )
        """,
    ]),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db():
    """Apply pending migrations. Call once at app startup."""
    global _db_path
    raw = config.SQLITE_DB_PATH
    if raw == ":memory:":
        _db_path = ":memory:"
    else:
        _db_path = os.path.abspath(raw)
        os.makedirs(os.path.dirname(_db_path), exist_ok=True)

    with _lock:
        conn = _connect()
        try:
            _apply_migrations(conn)
            conn.commit()
        finally:
            conn.close()
    logger.info("DB initialised at %s", _db_path)


def get_db() -> sqlite3.Connection:
    """Open a new connection. Use within a request via Flask g."""
    conn = _connect()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def return_db(conn: sqlite3.Connection):
    if conn:
        conn.close()


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    try:
        rows = conn.execute("SELECT version FROM db_version").fetchall()
        return {r["version"] for r in rows}
    except sqlite3.OperationalError:
        return set()


def _apply_migrations(conn: sqlite3.Connection):
    applied = _applied_versions(conn)
    for version, description, statements in _MIGRATIONS:
        if version in applied:
            continue
        logger.info("Applying migration v%d: %s", version, description)
        for sql in statements:
            conn.execute(sql)
        conn.execute(
            "INSERT INTO db_version (version, description) VALUES (?, ?)",
            (version, description),
        )
    conn.commit()
