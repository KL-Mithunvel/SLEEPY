"""
Integration tests for GET /api/admin/login-events — must be admin-only and
must reflect what's actually in the login_events table.
"""

import auth_utils
import local_db


def test_login_events_accessible_with_dev_bypass(client):
    # DEV_AUTH_BYPASS synthesizes an admin user — should be allowed through.
    resp = client.get("/api/admin/login-events")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "events" in data
    assert "total" in data


def test_login_events_forbidden_for_user_role(client, monkeypatch, create_user):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret-32-bytes-minimum-ok!")
    create_user("regular-joe", role="user")

    token = auth_utils.issue_token("regular-joe", "user")
    resp = client.get("/api/admin/login-events", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_login_events_allowed_for_admin_role(client, monkeypatch, create_user):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret-32-bytes-minimum-ok!")
    create_user("real-admin", role="admin")

    token = auth_utils.issue_token("real-admin", "admin")
    resp = client.get("/api/admin/login-events", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_login_events_bad_pagination_is_400_not_500(client):
    assert client.get("/api/admin/login-events?limit=abc").status_code == 400
    assert client.get("/api/admin/login-events?offset=-x").status_code == 400


def test_login_events_reflects_inserted_rows_most_recent_first(client):
    conn = local_db.get_db()
    try:
        conn.execute(
            "INSERT INTO login_events (username, success, ip_address, geo_city, geo_country) "
            "VALUES (?, ?, ?, ?, ?)",
            ("admin-view-test-1", 1, "203.0.113.1", "Mumbai", "India"),
        )
        conn.execute(
            "INSERT INTO login_events (username, success, ip_address, geo_city, geo_country) "
            "VALUES (?, ?, ?, ?, ?)",
            ("admin-view-test-2", 0, "203.0.113.2", None, None),
        )
        conn.commit()
    finally:
        local_db.return_db(conn)

    resp = client.get("/api/admin/login-events?limit=2")
    assert resp.status_code == 200
    events = resp.get_json()["events"]
    assert events[0]["username"] == "admin-view-test-2"
    assert events[1]["username"] == "admin-view-test-1"
    assert events[1]["geo_city"] == "Mumbai"


# ---------------------------------------------------------------------------
# GET /api/admin/ai-usage
# ---------------------------------------------------------------------------

def _insert_ai_event(conn, **kwargs):
    defaults = {
        "event_type": "ai_chat", "model": "claude-sonnet-4-6",
        "input_tokens": 100, "output_tokens": 50, "latency_ms": 800,
        "accepted": None, "voided": 0,
    }
    defaults.update(kwargs)
    conn.execute(
        """
        INSERT INTO ai_events (event_type, model, input_tokens, output_tokens, latency_ms, accepted, voided)
        VALUES (:event_type, :model, :input_tokens, :output_tokens, :latency_ms, :accepted, :voided)
        """,
        defaults,
    )
    conn.commit()


def test_ai_usage_accessible_with_dev_bypass(client):
    resp = client.get("/api/admin/ai-usage")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "summary" in data
    assert "by_model" in data
    assert "by_type" in data
    assert "daily" in data
    assert "recent" in data


def test_ai_usage_forbidden_for_user_role(client, monkeypatch, create_user):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret-32-bytes-minimum-ok!")
    create_user("regular-joe-2", role="user")

    token = auth_utils.issue_token("regular-joe-2", "user")
    resp = client.get("/api/admin/ai-usage", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_ai_usage_allowed_for_admin_role(client, monkeypatch, create_user):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret-32-bytes-minimum-ok!")
    create_user("real-admin-2", role="admin")

    token = auth_utils.issue_token("real-admin-2", "admin")
    resp = client.get("/api/admin/ai-usage", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_ai_usage_bad_pagination_is_400_not_500(client):
    assert client.get("/api/admin/ai-usage?limit=abc").status_code == 400
    assert client.get("/api/admin/ai-usage?offset=-x").status_code == 400


def test_ai_usage_aggregates_tokens_by_model(client):
    conn = local_db.get_db()
    try:
        _insert_ai_event(conn, model="claude-sonnet-4-6", input_tokens=100, output_tokens=50)
        _insert_ai_event(conn, model="claude-sonnet-4-6", input_tokens=200, output_tokens=80)
        _insert_ai_event(conn, model="claude-haiku-4-5", input_tokens=10, output_tokens=5)
    finally:
        local_db.return_db(conn)

    data = client.get("/api/admin/ai-usage").get_json()
    sonnet = next(m for m in data["by_model"] if m["model"] == "claude-sonnet-4-6")
    assert sonnet["calls"] >= 2
    assert sonnet["input_tokens"] >= 300
    assert sonnet["output_tokens"] >= 130


def test_ai_usage_excludes_voided_and_non_llm_rows(client):
    conn = local_db.get_db()
    try:
        _insert_ai_event(conn, model="claude-sonnet-4-6", input_tokens=999, output_tokens=999, voided=1)
        # A corpus-write proposal row (md_edit/move/delete) — no model, must not appear.
        conn.execute("INSERT INTO ai_events (event_type, diff) VALUES ('md_edit', '{}')")
        conn.commit()
    finally:
        local_db.return_db(conn)

    data = client.get("/api/admin/ai-usage").get_json()
    assert all(row["model"] is not None for row in data["recent"])
    # The voided row's huge token counts must not leak into the aggregate.
    for row in data["recent"]:
        assert row["input_tokens"] != 999


def test_ai_usage_recent_reflects_inserted_rows_most_recent_first(client):
    conn = local_db.get_db()
    try:
        _insert_ai_event(conn, event_type="ai_suggest", model="claude-sonnet-4-6")
        _insert_ai_event(conn, event_type="goal_planning", model="claude-haiku-4-5")
    finally:
        local_db.return_db(conn)

    resp = client.get("/api/admin/ai-usage?limit=2")
    assert resp.status_code == 200
    recent = resp.get_json()["recent"]
    assert recent[0]["event_type"] == "goal_planning"
    assert recent[1]["event_type"] == "ai_suggest"


# ---------------------------------------------------------------------------
# GET /api/admin/alerts
# ---------------------------------------------------------------------------

def _insert_alert(conn, key, *, suppressed=0, emailed=0, subject="subj", body="body"):
    conn.execute(
        "INSERT INTO system_alerts (alert_key, subject, body, suppressed, emailed) "
        "VALUES (?, ?, ?, ?, ?)",
        (key, subject, body, suppressed, emailed),
    )


def test_alerts_accessible_with_dev_bypass(client):
    resp = client.get("/api/admin/alerts")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "summary" in data and "by_key" in data and "recent" in data


def test_alerts_forbidden_for_user_role(client, monkeypatch, create_user):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret-32-bytes-minimum-ok!")
    create_user("alerts-joe", role="user")

    token = auth_utils.issue_token("alerts-joe", "user")
    resp = client.get("/api/admin/alerts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_alerts_allowed_for_admin_role(client, monkeypatch, create_user):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret-32-bytes-minimum-ok!")
    create_user("alerts-admin", role="admin")

    token = auth_utils.issue_token("alerts-admin", "admin")
    resp = client.get("/api/admin/alerts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_alerts_bad_pagination_is_400_not_500(client):
    assert client.get("/api/admin/alerts?limit=abc").status_code == 400
    assert client.get("/api/admin/alerts?offset=-x").status_code == 400


def test_alerts_counts_undelivered_separately_from_suppressed(client):
    """
    The whole point of this view: an alert that was raised but had nowhere to
    go (suppressed=0, emailed=0) must not be lumped in with one that was
    deliberately throttled (suppressed=1).
    """
    conn = local_db.get_db()
    try:
        _insert_alert(conn, "disk_space:low", suppressed=0, emailed=0)   # undeliverable
        _insert_alert(conn, "disk_space:low", suppressed=1, emailed=0)   # throttled
        _insert_alert(conn, "task_failed:md_reindex", suppressed=0, emailed=1)  # queued
        conn.commit()
    finally:
        local_db.return_db(conn)

    data = client.get("/api/admin/alerts").get_json()
    s = data["summary"]
    assert s["total"] == 3
    assert s["undelivered"] == 1
    assert s["suppressed"] == 1
    assert s["emailed"] == 1
    assert s["distinct_keys"] == 2


def test_alerts_by_key_groups_and_reports_undelivered(client):
    conn = local_db.get_db()
    try:
        _insert_alert(conn, "grouped:key", suppressed=0, emailed=0)
        _insert_alert(conn, "grouped:key", suppressed=0, emailed=0)
        _insert_alert(conn, "grouped:key", suppressed=0, emailed=1)
        conn.commit()
    finally:
        local_db.return_db(conn)

    data = client.get("/api/admin/alerts").get_json()
    row = next(r for r in data["by_key"] if r["alert_key"] == "grouped:key")
    assert row["count"] == 3
    assert row["undelivered"] == 2
    assert row["last_at"]


def test_alerts_recent_is_most_recent_first_and_carries_body(client):
    conn = local_db.get_db()
    try:
        _insert_alert(conn, "ordering:first", subject="older")
        _insert_alert(conn, "ordering:second", subject="newer", body="detail text")
        conn.commit()
    finally:
        local_db.return_db(conn)

    recent = client.get("/api/admin/alerts").get_json()["recent"]
    keys = [r["alert_key"] for r in recent]
    assert keys.index("ordering:second") < keys.index("ordering:first")
    newer = next(r for r in recent if r["alert_key"] == "ordering:second")
    assert newer["body"] == "detail text"
