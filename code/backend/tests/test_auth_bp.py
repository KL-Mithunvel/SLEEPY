"""
Integration tests for POST /api/auth/login and /api/auth/logout — the real
username/password path (DEV_AUTH_BYPASS is a separate, already-covered path
in test_rbac.py).
"""

import auth_utils
import local_db


def _create_user(username, password, role="user"):
    conn = local_db.get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, auth_utils.hash_password(password), role),
        )
        conn.commit()
    finally:
        local_db.return_db(conn)


def test_login_success_returns_usable_token(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret-32-bytes-minimum-ok!")
    _create_user("alice", "correct horse battery staple", role="user")

    resp = client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["username"] == "alice"
    assert data["role"] == "user"
    token = data["token"]

    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    me_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    assert me_resp.get_json()["sub"] == "alice"


def test_login_wrong_password_rejected_and_logged(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret-32-bytes-minimum-ok!")
    _create_user("bob", "the-real-password", role="user")

    resp = client.post("/api/auth/login", json={"username": "bob", "password": "wrong"})
    assert resp.status_code == 401
    assert "error" in resp.get_json()

    conn = local_db.get_db()
    try:
        row = conn.execute(
            "SELECT success FROM login_events WHERE username = 'bob' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["success"] == 0
    finally:
        local_db.return_db(conn)


def test_login_unknown_username_rejected(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret-32-bytes-minimum-ok!")
    resp = client.post("/api/auth/login", json={"username": "nobody-such-user", "password": "whatever"})
    assert resp.status_code == 401


def test_login_missing_fields_rejected(client):
    resp = client.post("/api/auth/login", json={"username": "alice"})
    assert resp.status_code == 400


def test_login_lockout_after_threshold(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", "test-secret-32-bytes-minimum-ok!")
    _create_user("carol", "the-real-password", role="user")

    for _ in range(auth_utils.LOCKOUT_MAX_ATTEMPTS):
        resp = client.post("/api/auth/login", json={"username": "carol", "password": "wrong"})
        assert resp.status_code == 401

    # One more attempt — even with the CORRECT password — should now be locked out.
    resp = client.post("/api/auth/login", json={"username": "carol", "password": "the-real-password"})
    assert resp.status_code == 429


def test_logout_returns_ok(client):
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
