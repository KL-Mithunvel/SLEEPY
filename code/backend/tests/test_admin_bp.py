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
