"""
Tests for the nightly SQLite backup mechanism: local_db.backup_database()
(WAL-safe online backup) and task_handlers._handle_db_backup (write + prune).
"""

import datetime
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")


# ---------------------------------------------------------------------------
# local_db.backup_database()
# ---------------------------------------------------------------------------

def test_backup_database_copies_data(tmp_path, monkeypatch):
    import local_db

    src_path = str(tmp_path / "src.db")
    src = sqlite3.connect(src_path)
    src.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    src.execute("INSERT INTO t (v) VALUES ('hello')")
    src.commit()
    src.close()

    monkeypatch.setattr(local_db, "_db_path", src_path)
    dest = str(tmp_path / "backups" / "copy.db")
    local_db.backup_database(dest)

    assert os.path.exists(dest)
    dst = sqlite3.connect(dest)
    try:
        row = dst.execute("SELECT v FROM t WHERE id = 1").fetchone()
        assert row[0] == "hello"
    finally:
        dst.close()


def test_backup_database_noop_for_memory_db(tmp_path, monkeypatch):
    import local_db
    monkeypatch.setattr(local_db, "_db_path", ":memory:")
    dest = str(tmp_path / "backups" / "copy.db")
    local_db.backup_database(dest)
    assert not os.path.exists(dest)


def test_backup_database_raises_if_not_initialised(tmp_path, monkeypatch):
    import local_db
    monkeypatch.setattr(local_db, "_db_path", None)
    with pytest.raises(RuntimeError, match="init_db"):
        local_db.backup_database(str(tmp_path / "x.db"))


def test_db_path_raises_if_not_initialised(monkeypatch):
    import local_db
    monkeypatch.setattr(local_db, "_db_path", None)
    with pytest.raises(RuntimeError, match="init_db"):
        local_db.db_path()


# ---------------------------------------------------------------------------
# task_handlers._handle_db_backup — write + retention pruning
# ---------------------------------------------------------------------------

@pytest.fixture()
def conn(app):
    import local_db
    return local_db.get_db()


def _age_file(path, days_old):
    t = (datetime.datetime.now() - datetime.timedelta(days=days_old)).timestamp()
    os.utime(path, (t, t))


def test_handle_db_backup_writes_new_file_and_prunes_old(tmp_path, monkeypatch, conn):
    import config
    import local_db
    import task_handlers

    fake_sqlite_path = str(tmp_path / "db" / "sqlite" / "pma.db")
    monkeypatch.setattr(local_db, "db_path", lambda: fake_sqlite_path)

    written = []

    def _fake_backup(dest_path):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "w") as f:
            f.write("x")
        written.append(dest_path)

    monkeypatch.setattr(local_db, "backup_database", _fake_backup)
    monkeypatch.setattr(config, "DB_BACKUP_RETENTION_DAYS", 14)

    backup_dir = tmp_path / "db" / "backups"
    os.makedirs(backup_dir, exist_ok=True)
    old_file = backup_dir / "pma-20250101-0000.db"
    old_file.write_text("old")
    _age_file(old_file, days_old=30)

    recent_file = backup_dir / "pma-20260101-0000.db"
    recent_file.write_text("recent")
    _age_file(recent_file, days_old=2)

    task_handlers._handle_db_backup({}, conn)

    assert len(written) == 1
    assert not old_file.exists()          # pruned
    assert recent_file.exists()           # kept
    remaining = {p.name for p in backup_dir.iterdir()}
    assert recent_file.name in remaining
    assert old_file.name not in remaining


def test_handle_db_backup_ignores_non_backup_files_when_pruning(tmp_path, monkeypatch, conn):
    import config
    import local_db
    import task_handlers

    fake_sqlite_path = str(tmp_path / "db" / "sqlite" / "pma.db")
    monkeypatch.setattr(local_db, "db_path", lambda: fake_sqlite_path)

    def _fake_backup(dest_path):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        open(dest_path, "w").close()

    monkeypatch.setattr(local_db, "backup_database", _fake_backup)
    monkeypatch.setattr(config, "DB_BACKUP_RETENTION_DAYS", 14)

    backup_dir = tmp_path / "db" / "backups"
    os.makedirs(backup_dir, exist_ok=True)
    unrelated = backup_dir / "readme.txt"
    unrelated.write_text("not a backup")
    _age_file(unrelated, days_old=999)

    task_handlers._handle_db_backup({}, conn)

    assert unrelated.exists()  # never touched — wrong prefix/suffix
