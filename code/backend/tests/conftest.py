import os
import sys
import pytest

# Add backend/ to path so imports work without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Force dev bypass and in-memory DB for all tests
os.environ["DEV_AUTH_BYPASS"] = "1"
os.environ["SQLITE_DB_PATH"] = ":memory:"


@pytest.fixture(scope="session")
def app():
    import local_db
    local_db.init_db()

    from app import app as flask_app
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture(scope="session")
def client(app):
    return app.test_client()


@pytest.fixture()
def owner_client(client):
    """Test client that presents as the admin role (dev bypass sets this automatically)."""
    return client


@pytest.fixture()
def create_user(app):
    """
    Insert (or replace) a row in `users`; returns (username, password, role).
    validate_token looks the user up on every real-token request (revocation
    check via token_version), so any test that mints a token with
    issue_token() for a non-bypass request must create the matching user first.
    """
    import auth_utils
    import local_db

    def _create(username, password="pw-for-tests-only", role="user"):
        conn = local_db.get_db()
        try:
            conn.execute("DELETE FROM users WHERE username = ?", (username,))
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, auth_utils.hash_password(password), role),
            )
            conn.commit()
        finally:
            local_db.return_db(conn)
        return username, password, role

    return _create
