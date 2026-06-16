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
    """Test client that presents as the owner role (dev bypass sets this automatically)."""
    return client
