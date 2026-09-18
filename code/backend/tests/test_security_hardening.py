"""
Regression tests for the 2026-09-16 security/robustness audit fixes:

- md_editor: stale-proposal guard (EditConflict), cross-process git lock,
  corpus .gitignore that keeps db/ out of the corpus repo
- task_queue/worker: stale 'running' tasks are reclaimed; failed handlers'
  partial DB writes are rolled back
- tools_registry: read_file/grep can't reach db/ or non-.md files
- auth: logout revokes tokens, lockout is keyed per (username, ip),
  login_events fields are bounded, tokens for deleted users / stale
  token_version are rejected, role comes from the DB not the token
- config: blank/short AUTH_SECRET_KEY refused whenever bypass is off;
  no Claude Code OAuth fallback remains
"""

import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

_BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..")
_SECRET = "test-secret-32-bytes-minimum-ok!"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def conn(app):
    import local_db
    return local_db.get_db()


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    import config
    root = str(tmp_path / "data")
    os.makedirs(root)
    monkeypatch.setattr(config, "USER_DATA_ROOT", root)
    return root


def _init_repo(data_root):
    import git
    repo = git.Repo.init(data_root)
    repo.config_writer().set_value("user", "name", "T").release()
    repo.config_writer().set_value("user", "email", "t@t.com").release()
    return repo


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# md_editor — stale-proposal guard
# ---------------------------------------------------------------------------

def test_apply_edit_refuses_when_file_changed_since_proposal(data_root, conn):
    import md_editor
    _init_repo(data_root)
    target = os.path.join(data_root, "inbox.md")
    _write(target, "# Inbox\n- [ ] one\n")

    result = md_editor.propose_edit("inbox.md", "# Inbox\n- [ ] one\n- [ ] two\n", "add two", conn)

    # Something else (news watch, quick capture, ...) writes the file meanwhile.
    with open(target, "a", encoding="utf-8") as f:
        f.write("- [ ] news bullet\n")

    with pytest.raises(md_editor.EditConflict, match="changed on disk"):
        md_editor.apply_edit(result["event_id"], conn)

    # Nothing was clobbered and the proposal is still pending (user can Discard).
    assert "news bullet" in _read(target)
    row = conn.execute("SELECT accepted FROM ai_events WHERE id = ?", (result["event_id"],)).fetchone()
    assert row["accepted"] is None


def test_apply_edit_refuses_when_new_file_appeared_since_proposal(data_root, conn):
    import md_editor
    _init_repo(data_root)
    result = md_editor.propose_edit("SMTW/fresh.md", "# Fresh\nContent", "create", conn)
    _write(os.path.join(data_root, "SMTW", "fresh.md"), "# Someone else made this first\n")
    with pytest.raises(md_editor.EditConflict):
        md_editor.apply_edit(result["event_id"], conn)


def test_apply_edit_still_applies_when_file_unchanged(data_root, conn):
    import md_editor
    _init_repo(data_root)
    target = os.path.join(data_root, "note.md")
    _write(target, "# Note\nold\n")
    result = md_editor.propose_edit("note.md", "# Note\nnew\n", "update", conn)
    sha = md_editor.apply_edit(result["event_id"], conn)
    assert len(sha) == 40
    assert _read(target) == "# Note\nnew\n"


def test_edit_conflict_surfaces_as_409(client, data_root, monkeypatch):
    import md_editor

    def _conflict(event_id, conn):
        raise md_editor.EditConflict("changed on disk")

    # The confirm route dispatches through apply_pending (which does its own
    # ai_events lookup before delegating to apply_edit/apply_move/apply_delete
    # by event_type) — mock the dispatcher itself so this test doesn't depend
    # on a real pending row existing for event_id=1.
    monkeypatch.setattr(md_editor, "apply_pending", _conflict)
    resp = client.post("/api/ai/edit/1/confirm")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# md_editor — corpus .gitignore + git lock
# ---------------------------------------------------------------------------

def test_apply_edit_writes_corpus_gitignore_excluding_db(data_root, conn):
    import md_editor
    _init_repo(data_root)
    result = md_editor.propose_edit("a.md", "# A\nx", "t", conn)
    md_editor.apply_edit(result["event_id"], conn)
    gi = _read(os.path.join(data_root, ".gitignore")).splitlines()
    assert "db/" in gi
    assert md_editor._GIT_LOCK_NAME in gi
    # Idempotent — a second call adds nothing.
    assert md_editor.ensure_corpus_gitignore(data_root) is False


def test_commit_data_root_never_commits_db_dir(data_root, monkeypatch):
    """The hourly commit_pending `git add -A` must not sweep the SQLite DB
    (password hashes, chat logs) into the corpus history."""
    import task_handlers
    repo = _init_repo(data_root)
    _write(os.path.join(data_root, "db", "sqlite", "pma.db"), "not really sqlite")
    _write(os.path.join(data_root, "SMTW", "p.md"), "# P\n")

    task_handlers._commit_data_root("test commit")

    tracked = repo.git.ls_files().splitlines()
    assert "SMTW/p.md" in tracked
    assert not any(p.startswith("db/") for p in tracked), tracked
    assert ".gitignore" in tracked


def test_corpus_git_lock_is_exclusive_and_recovers_stale(data_root, monkeypatch):
    import md_editor
    lock_path = os.path.join(data_root, md_editor._GIT_LOCK_NAME)
    monkeypatch.setattr(md_editor, "_GIT_LOCK_TIMEOUT_SEC", 0.5)

    with md_editor.corpus_git_lock(data_root):
        assert os.path.exists(lock_path)
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            with md_editor.corpus_git_lock(data_root):
                pass
        assert time.monotonic() - t0 >= 0.4
    assert not os.path.exists(lock_path)

    # A lock left behind by a crashed process (old mtime) is swept.
    _write(lock_path, "dead")
    old = time.time() - md_editor._GIT_LOCK_STALE_SEC - 10
    os.utime(lock_path, (old, old))
    with md_editor.corpus_git_lock(data_root):
        pass
    assert not os.path.exists(lock_path)


# ---------------------------------------------------------------------------
# project_editor.set_status hardening
# ---------------------------------------------------------------------------

def test_set_status_refuses_root_level_file_and_existing_target(data_root, conn):
    import project_editor
    _init_repo(data_root)
    _write(os.path.join(data_root, "inbox.md"), "---\nstatus: active\n---\n# Inbox\n")
    assert project_editor.set_status(data_root, "inbox.md", "archived", conn) is None

    _write(os.path.join(data_root, "SMTW", "p.md"), "---\nkey: p\nstatus: active\n---\n# P\n")
    _write(os.path.join(data_root, "SMTW", "Archive", "p.md"), "older archived copy")
    assert project_editor.set_status(data_root, "SMTW/p.md", "archived", conn) is None
    # Neither file was touched.
    assert _read(os.path.join(data_root, "SMTW", "Archive", "p.md")) == "older archived copy"
    assert os.path.isfile(os.path.join(data_root, "SMTW", "p.md"))


# ---------------------------------------------------------------------------
# task_queue / worker
# ---------------------------------------------------------------------------

def test_claim_next_reclaims_stale_running_task(conn):
    """A worker that died mid-task leaves the row 'running'. Once locked_until
    has passed it must become claimable again instead of being stuck forever."""
    import task_queue

    cur = conn.execute(
        """
        INSERT INTO task_queue (task_type, payload, status, attempts, scheduled_for, locked_until)
        VALUES ('stale_probe', '{}', 'running', 1,
                datetime('now', 'localtime', '-3 hours'),
                datetime('now', 'localtime', '-1 hour'))
        """
    )
    conn.commit()
    stale_id = cur.lastrowid

    seen = []
    while (t := task_queue.claim_next(conn)) is not None:
        seen.append(t["id"])
        task_queue.mark_done(conn, t["id"])
    assert stale_id in seen
    row = conn.execute("SELECT attempts FROM task_queue WHERE id = ?", (stale_id,)).fetchone()
    assert row["attempts"] == 2


def test_claim_next_does_not_steal_live_running_task(conn):
    import task_queue

    cur = conn.execute(
        """
        INSERT INTO task_queue (task_type, payload, status, attempts, scheduled_for, locked_until)
        VALUES ('live_probe', '{}', 'running', 1,
                datetime('now', 'localtime', '-1 minute'),
                datetime('now', 'localtime', '+20 minutes'))
        """
    )
    conn.commit()
    live_id = cur.lastrowid

    while (t := task_queue.claim_next(conn)) is not None:
        assert t["id"] != live_id
        task_queue.mark_done(conn, t["id"])
    row = conn.execute("SELECT status FROM task_queue WHERE id = ?", (live_id,)).fetchone()
    assert row["status"] == "running"


def test_failed_handler_writes_are_rolled_back(conn, monkeypatch):
    """Handlers never commit; on failure the worker must discard their partial
    writes rather than let mark_failed's commit persist a half-finished state."""
    import task_handlers
    import task_queue
    import worker

    def _boom(payload, c):
        c.execute("INSERT INTO ai_events (event_type, diff) VALUES ('rollback-probe', 'x')")
        raise RuntimeError("simulated handler crash")

    monkeypatch.setitem(task_handlers.HANDLERS, "rollback_probe", _boom)
    tid = task_queue.enqueue(conn, "rollback_probe", {})

    worker._drain_once()

    probe = conn.execute(
        "SELECT COUNT(*) AS n FROM ai_events WHERE event_type = 'rollback-probe'"
    ).fetchone()
    assert probe["n"] == 0
    row = conn.execute("SELECT status, last_error FROM task_queue WHERE id = ?", (tid,)).fetchone()
    assert row["status"] in ("pending", "failed")
    assert "simulated handler crash" in row["last_error"]


# ---------------------------------------------------------------------------
# tools_registry — read_file / grep boundaries
# ---------------------------------------------------------------------------

def _tool(conn, name):
    import tools_registry
    return next(t for t in tools_registry.build_tools(conn) if t.name == name)


@pytest.fixture()
def corpus(data_root):
    _write(os.path.join(data_root, "db", "sqlite", "pma.db"), "SQLite format 3 secret-hash-material")
    _write(os.path.join(data_root, "db", "news_seen.json"), "[]")
    _write(os.path.join(data_root, "db", "leak.md"), "needle in db\n")
    _write(os.path.join(data_root, "SMTW", "proj.md"), "# Proj\n\nneedle here\n")
    _write(os.path.join(data_root, "SMTW", "notes.txt"), "plain text")
    return data_root


def test_read_file_reads_corpus_md(conn, corpus):
    assert "needle here" in _tool(conn, "read_file").handler({"path": "SMTW/proj.md"})


def test_read_file_refuses_db_dir(conn, corpus):
    out = _tool(conn, "read_file").handler({"path": "db/sqlite/pma.db"})
    assert out.startswith("[error:")
    assert "secret-hash-material" not in out
    assert _tool(conn, "read_file").handler({"path": "db/news_seen.json"}).startswith("[error:")
    assert _tool(conn, "read_file").handler({"path": "db/leak.md"}).startswith("[error:")


def test_read_file_refuses_non_md_and_traversal(conn, corpus):
    out = _tool(conn, "read_file").handler({"path": "SMTW/notes.txt"})
    assert out.startswith("[error:")
    assert "plain text" not in out
    assert _tool(conn, "read_file").handler({"path": "../../etc/passwd.md"}).startswith("[error:")


def test_grep_skips_db_dir_and_caps_pattern(conn, corpus):
    out = _tool(conn, "grep").handler({"pattern": "needle"})
    assert "SMTW/proj.md" in out
    assert "db/leak.md" not in out
    assert _tool(conn, "grep").handler({"pattern": "needle", "path": "db"}).startswith("[error:")
    assert _tool(conn, "grep").handler({"pattern": "(a+)+" * 100}).startswith("[error:")


# ---------------------------------------------------------------------------
# today_bp — captures are single lines
# ---------------------------------------------------------------------------

def test_capture_collapses_newlines(client, data_root, monkeypatch):
    import md_editor
    captured = {}

    def _propose(rel_path, new_content, summary, conn):
        captured["content"] = new_content
        return {"event_id": 1}

    monkeypatch.setattr(md_editor, "propose_edit", _propose)
    monkeypatch.setattr(md_editor, "apply_edit", lambda eid, conn: "c" * 40)
    resp = client.post("/api/today/capture", json={"text": "call bob\n\n## Housekeeping\n- [ ] injected"})
    assert resp.status_code == 200
    # The text survives, but as ONE list line — never as a new heading line.
    assert not any(line.startswith("## Housekeeping") for line in captured["content"].splitlines())
    assert "- [ ] call bob ## Housekeeping - [ ] injected" in captured["content"]


# ---------------------------------------------------------------------------
# auth — revocation, lockout keying, bounded login_events, role from DB
# ---------------------------------------------------------------------------

def test_logout_revokes_all_tokens(client, monkeypatch, create_user):
    import config
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", _SECRET)
    create_user("dave", "the-real-password", role="user")

    token = client.post(
        "/api/auth/login", json={"username": "dave", "password": "the-real-password"}
    ).get_json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    resp = client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 401
    assert "revoked" in resp.get_json()["error"].lower()

    # A fresh login mints a token with the bumped version and works again.
    token2 = client.post(
        "/api/auth/login", json={"username": "dave", "password": "the-real-password"}
    ).get_json()["token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token2}"}).status_code == 200


def test_revoked_token_rejected_after_password_reset_style_bump(client, monkeypatch, create_user):
    import config
    import local_db
    from auth_utils import issue_token, revoke_all_tokens
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", _SECRET)
    create_user("revoke-me", role="user")

    token = issue_token("revoke-me", "user", token_version=0)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    conn = local_db.get_db()
    try:
        revoke_all_tokens(conn, "revoke-me")
    finally:
        local_db.return_db(conn)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_for_deleted_user_rejected(client, monkeypatch):
    import config
    from auth_utils import issue_token
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", _SECRET)
    token = issue_token("ghost-user-never-created", "admin")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_role_comes_from_db_not_token(client, monkeypatch, create_user):
    """A token claiming admin for a plain user is only ever worth 'user'."""
    import config
    from auth_utils import issue_token
    monkeypatch.setattr(config, "DEV_AUTH_BYPASS", False)
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", _SECRET)
    create_user("plain-user", role="user")
    token = issue_token("plain-user", "admin")
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.get_json()["role"] == "user"
    assert client.get("/api/admin/login-events", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_lockout_is_per_ip_not_just_per_username(client, monkeypatch, create_user):
    """An outsider hammering the known username from one address must not lock
    the real owner out from a different address."""
    import auth_utils
    import config
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", _SECRET)
    create_user("erin", "the-real-password", role="user")

    attacker = {"REMOTE_ADDR": "198.51.100.7"}
    owner = {"REMOTE_ADDR": "203.0.113.9"}
    for _ in range(auth_utils.LOCKOUT_MAX_ATTEMPTS):
        resp = client.post("/api/auth/login", json={"username": "erin", "password": "wrong"},
                           environ_base=attacker)
        assert resp.status_code == 401

    resp = client.post("/api/auth/login", json={"username": "erin", "password": "the-real-password"},
                       environ_base=attacker)
    assert resp.status_code == 429
    resp = client.post("/api/auth/login", json={"username": "erin", "password": "the-real-password"},
                       environ_base=owner)
    assert resp.status_code == 200


def test_per_ip_lockout_across_usernames(client, monkeypatch):
    import auth_utils
    import config
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", _SECRET)
    stuffer = {"REMOTE_ADDR": "198.51.100.42"}
    for i in range(auth_utils.LOCKOUT_IP_MAX_ATTEMPTS):
        client.post("/api/auth/login", json={"username": f"guess-{i}", "password": "x"}, environ_base=stuffer)
    resp = client.post("/api/auth/login", json={"username": "yet-another", "password": "x"}, environ_base=stuffer)
    assert resp.status_code == 429


def test_unlock_user_clears_lockout(client, monkeypatch, create_user):
    """manage_users.py unlock-user (auth_utils.unlock_user) lets a legitimate
    user back in immediately instead of waiting out LOCKOUT_WINDOW_MINUTES."""
    import auth_utils
    import config
    import local_db
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", _SECRET)
    create_user("dana", "the-real-password", role="user")

    who = {"REMOTE_ADDR": "203.0.113.50"}
    for _ in range(auth_utils.LOCKOUT_MAX_ATTEMPTS):
        client.post("/api/auth/login", json={"username": "dana", "password": "wrong"}, environ_base=who)
    resp = client.post("/api/auth/login", json={"username": "dana", "password": "the-real-password"}, environ_base=who)
    assert resp.status_code == 429

    conn = local_db.get_db()
    try:
        n = auth_utils.unlock_user(conn, "dana")
    finally:
        local_db.return_db(conn)
    assert n == auth_utils.LOCKOUT_MAX_ATTEMPTS

    resp = client.post("/api/auth/login", json={"username": "dana", "password": "the-real-password"}, environ_base=who)
    assert resp.status_code == 200


def test_unlock_user_preserves_audit_rows_voided_not_deleted(client, monkeypatch, create_user):
    """login_events stays append-only — unlock flags rows voided=1, never DELETEs."""
    import auth_utils
    import config
    import local_db
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", _SECRET)
    create_user("erin2", "the-real-password", role="user")

    who = {"REMOTE_ADDR": "203.0.113.51"}
    client.post("/api/auth/login", json={"username": "erin2", "password": "wrong"}, environ_base=who)

    conn = local_db.get_db()
    try:
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM login_events WHERE username = 'erin2'"
        ).fetchone()["n"]
        auth_utils.unlock_user(conn, "erin2")
        after = conn.execute(
            "SELECT COUNT(*) AS n FROM login_events WHERE username = 'erin2'"
        ).fetchone()["n"]
        voided = conn.execute(
            "SELECT COUNT(*) AS n FROM login_events WHERE username = 'erin2' AND voided = 1"
        ).fetchone()["n"]
    finally:
        local_db.return_db(conn)
    assert before == after  # nothing deleted
    assert voided == before  # everything flagged instead


def test_unlock_user_does_not_affect_other_usernames(client, monkeypatch, create_user):
    import auth_utils
    import config
    import local_db
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", _SECRET)
    create_user("frank2", "pw-frank", role="user")
    create_user("gina", "pw-gina", role="user")

    ip = {"REMOTE_ADDR": "203.0.113.52"}
    for _ in range(auth_utils.LOCKOUT_MAX_ATTEMPTS):
        client.post("/api/auth/login", json={"username": "gina", "password": "wrong"}, environ_base=ip)

    conn = local_db.get_db()
    try:
        auth_utils.unlock_user(conn, "frank2")  # frank2 never failed — 0 rows to void
    finally:
        local_db.return_db(conn)

    resp = client.post("/api/auth/login", json={"username": "gina", "password": "pw-gina"}, environ_base=ip)
    assert resp.status_code == 429  # gina is still locked out


def test_manage_users_unlock_user_cli(monkeypatch, app, create_user):
    """The manage_users.py CLI path, not just the auth_utils helper."""
    import auth_utils
    import config
    import local_db
    import manage_users
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", _SECRET)
    create_user("holly", "pw-holly", role="user")

    conn = local_db.get_db()
    try:
        for _ in range(3):
            conn.execute(
                "INSERT INTO login_events (username, success, ip_address) VALUES ('holly', 0, '203.0.113.53')"
            )
        conn.commit()
    finally:
        local_db.return_db(conn)

    manage_users.unlock_user("holly")

    conn = local_db.get_db()
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM login_events WHERE username = 'holly' AND success = 0 AND voided = 0"
        ).fetchone()["n"]
    finally:
        local_db.return_db(conn)
    assert remaining == 0


def test_login_events_fields_are_bounded(client, monkeypatch):
    import config
    import local_db
    monkeypatch.setattr(config, "AUTH_SECRET_KEY", _SECRET)
    resp = client.post("/api/auth/login", json={"username": "frank", "password": "x"},
                       headers={"User-Agent": "U" * 5000})
    assert resp.status_code == 401
    conn = local_db.get_db()
    try:
        row = conn.execute(
            "SELECT LENGTH(user_agent) AS n FROM login_events WHERE username='frank' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["n"] <= 512
    finally:
        local_db.return_db(conn)
    # An absurdly long username never reaches the DB at all.
    assert client.post("/api/auth/login", json={"username": "u" * 300, "password": "x"}).status_code == 401


def test_unknown_username_still_runs_a_hash_check(monkeypatch):
    """Timing-enumeration guard: the miss path must call check_password_hash too."""
    import auth_utils
    calls = []
    monkeypatch.setattr(auth_utils, "check_password_hash", lambda h, p: calls.append(h) or False)
    assert auth_utils.verify_login(None, "whatever") is False
    assert calls == [auth_utils._DUMMY_HASH]


# ---------------------------------------------------------------------------
# config guards (fresh subprocess — config raises at import)
# ---------------------------------------------------------------------------

def _run_import(env_overrides: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import config"],
        cwd=_BACKEND_DIR, env=env, capture_output=True, text=True, timeout=30,
    )


def test_bypass_off_with_blank_secret_refuses_even_in_dev():
    result = _run_import({
        "APP_ENV": "development", "DEV_AUTH_BYPASS": "0",
        "ANTHROPIC_API_KEY": "sk-ant-fake", "AUTH_SECRET_KEY": "", "SQLITE_DB_PATH": ":memory:",
    })
    assert result.returncode != 0
    assert "AUTH_SECRET_KEY" in result.stderr


def test_short_secret_refuses_to_start():
    result = _run_import({
        "APP_ENV": "development", "DEV_AUTH_BYPASS": "0",
        "ANTHROPIC_API_KEY": "sk-ant-fake", "AUTH_SECRET_KEY": "too-short", "SQLITE_DB_PATH": ":memory:",
    })
    assert result.returncode != 0
    assert "too short" in result.stderr


def test_no_claude_code_oauth_fallback_remains():
    # Strip comments so the explanatory note about the removal doesn't trip this.
    def _code_only(path):
        return "\n".join(
            line for line in _read(path).splitlines() if not line.lstrip().startswith("#")
        )
    src = _code_only(os.path.join(_BACKEND_DIR, "config.py"))
    assert "ANTHROPIC_AUTH_TOKEN" not in src
    assert "credentials.json" not in src
    assert "Path.home()" not in src
    llm_src = _code_only(os.path.join(_BACKEND_DIR, "llm.py"))
    assert "auth_token=" not in llm_src


# ---------------------------------------------------------------------------
# Symlink escape — every path guard resolves symlinks, not just `..`
# ---------------------------------------------------------------------------

def test_symlink_inside_corpus_cannot_escape(conn, data_root, tmp_path, client):
    """
    A symlink planted inside the corpus (a corpus-repo checkout that ever
    tracked one, or a hand-made `ln -s` on the box) must not let any reader
    or writer reach outside the data root — normpath+prefix alone follows it.
    """
    import md_editor
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("# outside-marker\n", encoding="utf-8")
    link = os.path.join(data_root, "Linked")
    try:
        os.symlink(str(outside), link, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Windows needs a privilege for symlinks; a directory junction needs
        # none and os.path.realpath resolves it the same way.
        if sys.platform != "win32":
            pytest.skip("symlink creation not permitted on this machine")
        subprocess.run(["cmd", "/c", "mklink", "/J", link, str(outside)],
                       check=True, capture_output=True)

    with pytest.raises(ValueError, match="traversal"):
        md_editor.validate_path("Linked/secret.md")

    out = _tool(conn, "read_file").handler({"path": "Linked/secret.md"})
    assert out.startswith("[error:")
    assert "outside-marker" not in out
    assert _tool(conn, "list_files").handler({"path": "Linked"}).startswith("[error:")

    resp = client.get("/api/projects/content?path=Linked/secret.md")
    assert resp.status_code == 400

    resp = client.get("/api/logs/content?path=Linked/Daily/2026-01-01.md")
    assert resp.status_code in (400, 404)
