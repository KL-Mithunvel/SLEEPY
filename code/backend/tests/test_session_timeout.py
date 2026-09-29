"""Short-lived tokens, /api/auth/refresh keep-alive, and the idle-timeout config."""

import datetime

import jwt

_SECRET = "session-timeout-test-secret-0123456789abcdef"


def _login(client, monkeypatch, create_user, name="erin"):
    import config
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", _SECRET)
    create_user(name, "the-real-password", role="user")
    token = client.post(
        "/api/auth/login", json={"username": name, "password": "the-real-password"}
    ).get_json()["token"]
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    return token, {"Authorization": f"Bearer {token}"}


def test_token_lifetime_is_minutes_not_days(monkeypatch):
    import auth_utils
    import config
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", _SECRET)
    monkeypatch.setattr(config, "AUTH_TOKEN_TTL_MINUTES", 20)
    payload = jwt.decode(auth_utils.issue_token("klm", "user"), _SECRET, algorithms=["HS256"])
    lifetime = datetime.datetime.fromtimestamp(payload["exp"]) - datetime.datetime.fromtimestamp(payload["iat"])
    assert lifetime == datetime.timedelta(minutes=20)


def test_token_ttl_outlasts_idle_timeout_plus_renewal_gap(monkeypatch):
    """The token must outlive the idle timeout plus the 4-min renewal gap, or the
    session could die before the 'are you still there?' prompt ever shows."""
    import config
    assert config.AUTH_TOKEN_TTL_MINUTES >= config.AUTH_IDLE_TIMEOUT_MINUTES + 4


def test_auth_config_exposes_idle_timeout(client):
    import config
    body = client.get("/api/auth/config").get_json()
    assert body["idleTimeoutMinutes"] == config.AUTH_IDLE_TIMEOUT_MINUTES


def test_refresh_returns_a_working_token(client, monkeypatch, create_user):
    _, headers = _login(client, monkeypatch, create_user)
    resp = client.post("/api/auth/refresh", headers=headers)
    assert resp.status_code == 200
    new_token = resp.get_json()["token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200


def test_refresh_requires_a_token(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    assert client.post("/api/auth/refresh").status_code == 401


def test_refresh_rejects_expired_token(client, monkeypatch, create_user):
    import auth_utils
    import config
    _login(client, monkeypatch, create_user)
    monkeypatch.setattr(config, "AUTH_TOKEN_TTL_MINUTES", -1)
    expired = auth_utils.issue_token("erin", "user")
    resp = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


def test_refresh_rejects_revoked_token(client, monkeypatch, create_user):
    _, headers = _login(client, monkeypatch, create_user)
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.post("/api/auth/refresh", headers=headers).status_code == 401


def test_logout_revokes_refreshed_tokens_too(client, monkeypatch, create_user):
    """A refreshed token shares the token_version, so one logout kills them all."""
    _, headers = _login(client, monkeypatch, create_user)
    newer = client.post("/api/auth/refresh", headers=headers).get_json()["token"]
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {newer}"}).status_code == 401


def test_logout_without_token_is_401(client, monkeypatch):
    """Why the frontend must send the Bearer header on logout: without it nothing is revoked."""
    import config
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    assert client.post("/api/auth/logout").status_code == 401


# ---------------------------------------------------------------------------
# Maximum session length (2026-09-30 sandbox round 2): idle sign-out is
# browser-only, so without a server-side cap a copied token could be kept
# alive forever by refreshing it every few minutes.
# ---------------------------------------------------------------------------

def _claims(token):
    return jwt.decode(token, _SECRET, algorithms=["HS256"])


def test_login_token_records_auth_time(client, monkeypatch, create_user):
    import time
    token, _ = _login(client, monkeypatch, create_user)
    assert abs(_claims(token)["auth_time"] - time.time()) < 60


def test_refresh_keeps_the_original_auth_time(client, monkeypatch, create_user):
    token, headers = _login(client, monkeypatch, create_user)
    newer = client.post("/api/auth/refresh", headers=headers).get_json()["token"]
    assert _claims(newer)["auth_time"] == _claims(token)["auth_time"]


def _token_logged_in_hours_ago(client, monkeypatch, create_user, hours):
    import time
    import auth_utils
    _login(client, monkeypatch, create_user)
    return auth_utils.issue_token("erin", "user", auth_time=int(time.time() - hours * 3600))


def test_refresh_refused_past_max_session_length(client, monkeypatch, create_user):
    import config
    monkeypatch.setattr(config, "AUTH_SESSION_MAX_HOURS", 12)
    old = _token_logged_in_hours_ago(client, monkeypatch, create_user, 13)
    resp = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {old}"})
    assert resp.status_code == 401


def test_refresh_allowed_inside_max_session_length(client, monkeypatch, create_user):
    import config
    monkeypatch.setattr(config, "AUTH_SESSION_MAX_HOURS", 12)
    recent = _token_logged_in_hours_ago(client, monkeypatch, create_user, 11)
    resp = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {recent}"})
    assert resp.status_code == 200


def test_token_without_auth_time_is_bounded_by_its_iat(client, monkeypatch, create_user):
    """Tokens minted before auth_time existed: the chain starts counting from iat."""
    import time
    import config
    _login(client, monkeypatch, create_user)
    monkeypatch.setattr(config, "AUTH_SESSION_MAX_HOURS", 12)
    now = int(time.time())
    legacy = jwt.encode({"sub": "erin", "role": "user", "ver": 0, "iat": now - 13 * 3600,
                         "exp": now + 600}, _SECRET, algorithm="HS256")
    resp = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {legacy}"})
    assert resp.status_code == 401
