"""
Tests for system_stats.py and GET /api/admin/system.

The theme running through these: this is the page you open when the box is
already sick, so the thing most worth testing is that it degrades instead of
failing. Every "unreadable" path must return None and still render.
"""

import json
import os

import pytest

import local_db
import system_stats


# ---------------------------------------------------------------------------
# Docker size parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("0B", 0),
    ("101.6MB", int(101.6 * 1024 ** 2)),
    ("4.55GB", int(4.55 * 1024 ** 3)),
    ("2.686GB", int(2.686 * 1024 ** 3)),
    ("1.5TB", int(1.5 * 1024 ** 4)),
    ("512KB", 512 * 1024),
])
def test_parse_docker_size_units(text, expected):
    assert system_stats.parse_docker_size(text) == expected


def test_parse_docker_size_takes_leading_token_of_reclaimable():
    # docker prints Reclaimable as "3.91GB (85%)" — the percentage must not
    # confuse the parse.
    assert system_stats.parse_docker_size("3.91GB (85%)") == int(3.91 * 1024 ** 3)


@pytest.mark.parametrize("bad", [None, "", "  ", "lots", "GB", 42, [], "NaNGB"])
def test_parse_docker_size_returns_none_not_zero_on_junk(bad):
    # None and 0 must stay distinguishable: 0 means "measured, empty",
    # None means "could not read". Collapsing them would make a broken
    # snapshot look like a clean one.
    assert system_stats.parse_docker_size(bad) is None


# ---------------------------------------------------------------------------
# collect()
# ---------------------------------------------------------------------------

def test_collect_returns_full_shape(tmp_path):
    snap = system_stats.collect(str(tmp_path))
    assert set(snap) == {"disk", "memory", "load_average", "uptime_seconds", "areas"}
    assert set(snap["disk"]) == {
        "total_bytes", "used_bytes", "free_bytes", "used_pct", "alert_pct"
    }
    assert set(snap["areas"]) == {
        "corpus_bytes", "db_bytes", "backups_bytes", "chroma_bytes"
    }


def test_collect_measures_corpus_bytes(tmp_path):
    (tmp_path / "a.md").write_text("x" * 500, encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("y" * 300, encoding="utf-8")

    snap = system_stats.collect(str(tmp_path))
    assert snap["areas"]["corpus_bytes"] == 800


def test_collect_does_not_subtract_dirs_outside_the_root(tmp_path, monkeypatch):
    """
    Regression: CHROMA_PATH is independently configurable and in prod belongs
    to another container. Subtracting a directory the corpus walk never
    visited drove corpus_bytes to 0 on the dev box.
    """
    import config

    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    (corpus_root / "a.md").write_text("x" * 800, encoding="utf-8")

    elsewhere = tmp_path / "chroma-somewhere-else"
    elsewhere.mkdir()
    (elsewhere / "index.bin").write_text("z" * 5000, encoding="utf-8")

    monkeypatch.setattr(config, "CHROMA_PATH", str(elsewhere))
    snap = system_stats.collect(str(corpus_root))

    assert snap["areas"]["corpus_bytes"] == 800        # not 0
    assert snap["areas"]["chroma_bytes"] == 5000       # still reported


def test_collect_does_subtract_nested_db_dirs(tmp_path, monkeypatch):
    """The inverse: a chroma dir INSIDE the root was counted by the walk, so
    it must be subtracted exactly once."""
    import config

    corpus_root = tmp_path / "corpus"
    (corpus_root / "db" / "chroma").mkdir(parents=True)
    (corpus_root / "a.md").write_text("x" * 800, encoding="utf-8")
    (corpus_root / "db" / "chroma" / "index.bin").write_text("z" * 5000, encoding="utf-8")

    monkeypatch.setattr(config, "CHROMA_PATH", str(corpus_root / "db" / "chroma"))
    snap = system_stats.collect(str(corpus_root))

    assert snap["areas"]["corpus_bytes"] == 800
    assert snap["areas"]["chroma_bytes"] == 5000


def test_collect_never_raises_on_missing_root():
    # A data root that does not exist is exactly the situation where this page
    # matters most; it must still answer.
    snap = system_stats.collect(os.path.join("nope", "definitely-not-here"))
    assert snap["areas"]["corpus_bytes"] is None
    assert snap["disk"]["total_bytes"] is not None      # falls back to cwd


def test_collect_disk_percent_is_sane(tmp_path):
    disk = system_stats.collect(str(tmp_path))["disk"]
    assert 0 <= disk["used_pct"] <= 100
    assert disk["alert_pct"] == 85


def test_dir_bytes_none_for_missing_but_zero_for_empty(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert system_stats._dir_bytes(str(empty)) == 0
    assert system_stats._dir_bytes(str(tmp_path / "gone")) is None


# ---------------------------------------------------------------------------
# Sampling + trend
# ---------------------------------------------------------------------------

def test_record_sample_inserts_a_row(app, tmp_path):
    conn = local_db.get_db()
    try:
        before = conn.execute("SELECT COUNT(*) AS n FROM system_metrics").fetchone()["n"]
        system_stats.record_sample(conn, str(tmp_path))
        after = conn.execute("SELECT COUNT(*) AS n FROM system_metrics").fetchone()["n"]
        assert after == before + 1

        row = conn.execute(
            "SELECT * FROM system_metrics ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["disk_used_pct"] is not None
        assert row["created_at"]
    finally:
        local_db.return_db(conn)


def test_record_sample_does_not_commit(app, tmp_path):
    """Handlers must not commit — the worker owns the transaction boundary."""
    conn = local_db.get_db()
    try:
        system_stats.record_sample(conn, str(tmp_path))
        conn.rollback()
        leftover = conn.execute(
            "SELECT COUNT(*) AS n FROM system_metrics WHERE created_at >= datetime('now','localtime','-1 minute')"
        ).fetchone()["n"]
        assert leftover == 0
    finally:
        local_db.return_db(conn)


def test_read_trend_groups_by_day(app):
    conn = local_db.get_db()
    try:
        conn.execute("DELETE FROM system_metrics")
        for day, pct in (("2026-09-18", 60.0), ("2026-09-18", 64.0), ("2026-09-19", 70.0)):
            conn.execute(
                "INSERT INTO system_metrics (disk_used_pct, disk_free_bytes, created_at) VALUES (?, ?, ?)",
                (pct, 1000, f"{day} 04:00:00"),
            )
        conn.commit()

        trend = system_stats.read_trend(conn, days=14)
        assert [t["day"] for t in trend] == ["2026-09-18", "2026-09-19"]   # oldest first
        assert trend[0]["samples"] == 2
        assert trend[0]["min_pct"] == 60.0
        assert trend[0]["max_pct"] == 64.0
    finally:
        conn.execute("DELETE FROM system_metrics")
        conn.commit()
        local_db.return_db(conn)


# ---------------------------------------------------------------------------
# Deploy snapshot
# ---------------------------------------------------------------------------

def _write_snapshot(root, payload):
    db_dir = os.path.join(root, "db")
    os.makedirs(db_dir, exist_ok=True)
    with open(os.path.join(db_dir, system_stats.DEPLOY_SNAPSHOT_NAME), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def test_read_deploy_snapshot_absent_is_none(tmp_path):
    assert system_stats.read_deploy_snapshot(str(tmp_path)) is None


def test_read_deploy_snapshot_normalises_docker_rows(tmp_path):
    _write_snapshot(str(tmp_path), {
        "captured_at": "2026-09-20T22:30:00+05:30",
        "commit": "09c2aa8",
        "docker": [
            {"Type": "Images", "Size": "4.55GB", "Reclaimable": "3.91GB (85%)"},
            {"Type": "Build Cache", "Size": "101.6MB", "Reclaimable": "101.6MB"},
            {"Type": "Local Volumes", "Size": "3.003MB", "Reclaimable": "0B (0%)"},
        ],
    })
    snap = system_stats.read_deploy_snapshot(str(tmp_path))
    assert snap["images_bytes"] == int(4.55 * 1024 ** 3)
    assert snap["images_reclaimable_bytes"] == int(3.91 * 1024 ** 3)
    assert snap["build_cache_bytes"] == int(101.6 * 1024 ** 2)
    assert snap["volumes_bytes"] == int(3.003 * 1024 ** 2)
    assert snap["captured_at"].startswith("2026-09-20")


def test_read_deploy_snapshot_survives_malformed_file(tmp_path):
    # Written by a shell script outside this process — must never break the page.
    db_dir = os.path.join(str(tmp_path), "db")
    os.makedirs(db_dir, exist_ok=True)
    with open(os.path.join(db_dir, system_stats.DEPLOY_SNAPSHOT_NAME), "w", encoding="utf-8") as fh:
        fh.write('{"captured_at": "oops", "docker": [')      # truncated JSON
    assert system_stats.read_deploy_snapshot(str(tmp_path)) is None


def test_read_deploy_snapshot_tolerates_missing_docker_key(tmp_path):
    _write_snapshot(str(tmp_path), {"captured_at": "2026-09-20T22:30:00+05:30"})
    snap = system_stats.read_deploy_snapshot(str(tmp_path))
    assert snap is not None
    assert snap["images_bytes"] is None
    assert snap["build_cache_bytes"] is None


# ---------------------------------------------------------------------------
# summarise()
# ---------------------------------------------------------------------------

def _current(pct, free_gb=2.4, total_gb=10.0):
    gb = 1024 ** 3
    return {
        "disk": {
            "used_pct": pct,
            "free_bytes": int(free_gb * gb),
            "total_bytes": int(total_gb * gb),
            "used_bytes": int((total_gb - free_gb) * gb),
            "alert_pct": 85,
        },
        "memory": {"total_bytes": None, "available_bytes": None, "used_pct": None},
        "areas": {"corpus_bytes": None, "db_bytes": None,
                  "backups_bytes": None, "chroma_bytes": None},
    }


def test_summarise_mentions_percentage_and_threshold():
    lines = system_stats.summarise(_current(76.0), [], None)
    assert any("76%" in ln for ln in lines)
    assert any("85%" in ln for ln in lines)


def test_summarise_flags_crossing_the_alert_threshold():
    lines = system_stats.summarise(_current(94.0), [], None)
    assert any("past the 85% alert threshold" in ln for ln in lines)


def test_summarise_reports_growth_between_first_and_last_day():
    trend = [
        {"day": "2026-09-18", "max_pct": 65.0, "min_pct": 65.0, "avg_pct": 65.0},
        {"day": "2026-09-19", "max_pct": 70.0, "min_pct": 66.0, "avg_pct": 68.0},
        {"day": "2026-09-20", "max_pct": 76.0, "min_pct": 70.0, "avg_pct": 73.0},
    ]
    lines = system_stats.summarise(_current(76.0), trend, None)
    assert any("grown" in ln and "65%" in ln and "76%" in ln for ln in lines)


def test_summarise_says_steady_when_flat():
    trend = [
        {"day": "2026-09-19", "max_pct": 65.0, "min_pct": 65.0, "avg_pct": 65.0},
        {"day": "2026-09-20", "max_pct": 65.2, "min_pct": 65.0, "avg_pct": 65.1},
    ]
    lines = system_stats.summarise(_current(65.0), trend, None)
    assert any("held steady" in ln for ln in lines)


def test_summarise_calls_out_a_large_build_cache():
    # The exact failure of 2026-09-20: image prune looked clean while 7.2GB of
    # build cache sat there. If the snapshot shows that again, say so.
    deploy = {
        "captured_at": "2026-09-20T22:30:00+05:30",
        "build_cache_bytes": int(7.2 * 1024 ** 3),
        "images_bytes": int(2.7 * 1024 ** 3),
    }
    lines = system_stats.summarise(_current(94.0), [], deploy)
    assert any("build cache" in ln.lower() and "prune" in ln.lower() for ln in lines)


def test_summarise_handles_totally_unreadable_disk():
    blank = {"disk": {"used_pct": None, "free_bytes": None, "total_bytes": None,
                      "used_bytes": None, "alert_pct": 85},
             "memory": {"used_pct": None}, "areas": {}}
    lines = system_stats.summarise(blank, [], None)
    assert lines and "could not be read" in lines[0]


def test_summarise_says_when_there_is_no_trend_yet():
    lines = system_stats.summarise(_current(50.0), [], None)
    assert any("Not enough samples" in ln for ln in lines)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

def test_system_endpoint_returns_expected_shape(owner_client):
    resp = owner_client.get("/api/admin/system")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) == {"current", "trend", "deploy", "summary"}
    assert isinstance(body["summary"], list) and body["summary"]
    assert "disk" in body["current"]


def test_system_endpoint_rejects_bad_days(owner_client):
    assert owner_client.get("/api/admin/system?days=abc").status_code == 400


def test_system_endpoint_clamps_days(owner_client):
    # Out-of-range values are clamped, not rejected — a stray ?days=9999 in a
    # bookmark should still render a page.
    assert owner_client.get("/api/admin/system?days=9999").status_code == 200
    assert owner_client.get("/api/admin/system?days=0").status_code == 200


def test_system_endpoint_requires_permission(client, create_user):
    """A plain 'user' must not see host internals; only admin gets admin:system."""
    import auth_utils
    username, _pw, _role = create_user("plainuser", role="user")
    token = auth_utils.issue_token(username, "user")

    import app as app_module
    original = app_module.app.config.get("TESTING")
    try:
        os.environ["DEV_AUTH_BYPASS"] = "0"
        import importlib
        import config
        importlib.reload(config)
        resp = client.get("/api/admin/system", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (401, 403)
    finally:
        os.environ["DEV_AUTH_BYPASS"] = "1"
        import importlib
        import config
        importlib.reload(config)
        app_module.app.config["TESTING"] = original
