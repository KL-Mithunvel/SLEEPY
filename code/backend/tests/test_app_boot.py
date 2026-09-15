"""
Regression test: app.py must initialize the DB itself on import.

Every other test in this suite relies on conftest.py's `app` fixture,
which calls local_db.init_db() BEFORE importing app.py — that masks the
exact gap this test exists to catch. Production (gunicorn importing
app:app directly) has no such wrapper: if app.py doesn't call init_db()
itself, every DB-touching route 500s with "_db_path is None" the moment
gunicorn starts serving requests, invisible to the whole rest of this
suite. This actually happened — caught on a live deploy, not in CI.
"""

import importlib


def test_app_module_initializes_db_on_import(app):
    import local_db

    # Simulate a fresh process where nothing has called init_db() yet.
    local_db._db_path = None
    assert local_db._db_path is None

    import app as app_module
    importlib.reload(app_module)

    assert local_db._db_path is not None
