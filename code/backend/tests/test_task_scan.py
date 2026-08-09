"""
Tests for task_scan.py — deterministic active-project task scanning and toggle.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

import task_scan  # noqa: E402


@pytest.fixture()
def conn(app):
    import local_db
    return local_db.get_db()


# ---------------------------------------------------------------------------
# scan_open_tasks
# ---------------------------------------------------------------------------

def _write_project(root, ou, fname, content):
    d = os.path.join(root, ou)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, fname), "w", encoding="utf-8") as f:
        f.write(content)


def test_scan_finds_open_tasks_in_active_project(tmp_path):
    _write_project(
        str(tmp_path), "SMTW", "proj.md",
        "---\nkey: proj\nstatus: active\ntitle: Proj\n---\n\n"
        "## Tasks\n\n- [ ] First task\n- [x] Done task\n- [ ] Second task due:2026-07-10\n",
    )
    tasks = task_scan.scan_open_tasks(str(tmp_path))
    texts = [t["text"] for t in tasks]
    assert "First task" in texts
    assert "Second task due:2026-07-10" in texts
    assert not any("Done task" in t for t in texts)


def test_scan_skips_inactive_project(tmp_path):
    _write_project(
        str(tmp_path), "SMTW", "proj.md",
        "---\nkey: proj\nstatus: archived\n---\n\n- [ ] Should not appear\n",
    )
    assert task_scan.scan_open_tasks(str(tmp_path)) == []


def test_scan_includes_project_with_no_status_field(tmp_path):
    _write_project(str(tmp_path), "SMTW", "proj.md", "# Proj\n\n- [ ] Untracked status task\n")
    tasks = task_scan.scan_open_tasks(str(tmp_path))
    assert len(tasks) == 1
    assert tasks[0]["text"] == "Untracked status task"


def test_scan_sorts_dated_tasks_ascending(tmp_path):
    _write_project(
        str(tmp_path), "SMTW", "proj.md",
        "---\nstatus: active\ntitle: Proj\n---\n\n"
        "- [ ] No date task\n"
        "- [ ] Later task due:2026-08-01\n"
        "- [ ] Sooner task due:2026-07-03\n",
    )
    tasks = task_scan.scan_open_tasks(str(tmp_path))
    dues = [t["due"] for t in tasks]
    assert dues == ["2026-07-03", "2026-08-01", None]


def test_scan_extracts_due_date(tmp_path):
    _write_project(
        str(tmp_path), "SMTW", "proj.md",
        "---\nstatus: active\n---\n\n- [ ] Task due:2026-07-15\n",
    )
    tasks = task_scan.scan_open_tasks(str(tmp_path))
    assert tasks[0]["due"] == "2026-07-15"


# ---------------------------------------------------------------------------
# toggle_task
# ---------------------------------------------------------------------------

def test_toggle_task_flips_checkbox(tmp_path, conn, monkeypatch):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path))  # md_editor writes here
    _write_project(
        str(tmp_path), "SMTW", "proj.md",
        "---\nstatus: active\n---\n\n- [ ] Deploy server\n- [ ] Write tests\n",
    )
    ok = task_scan.toggle_task(str(tmp_path), "SMTW/proj.md", "Deploy server", conn)
    assert ok is True

    content = (tmp_path / "SMTW" / "proj.md").read_text(encoding="utf-8")
    assert "- [x] Deploy server" in content
    assert "- [ ] Write tests" in content   # other task untouched


def test_toggle_task_no_match_returns_false(tmp_path, conn, monkeypatch):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path))
    _write_project(str(tmp_path), "SMTW", "proj.md", "---\nstatus: active\n---\n\n- [ ] Real task\n")
    ok = task_scan.toggle_task(str(tmp_path), "SMTW/proj.md", "Nonexistent task", conn)
    assert ok is False


def test_toggle_task_missing_file_returns_false(tmp_path, conn, monkeypatch):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path))
    ok = task_scan.toggle_task(str(tmp_path), "SMTW/missing.md", "Anything", conn)
    assert ok is False


# ---------------------------------------------------------------------------
# cancel_task
# ---------------------------------------------------------------------------

def test_cancel_task_marks_cancelled_not_done(tmp_path, conn, monkeypatch):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path))
    _write_project(
        str(tmp_path), "SMTW", "proj.md",
        "---\nstatus: active\n---\n\n- [ ] Deploy server\n- [ ] Write tests\n",
    )
    ok = task_scan.cancel_task(str(tmp_path), "SMTW/proj.md", "Deploy server", conn)
    assert ok is True

    content = (tmp_path / "SMTW" / "proj.md").read_text(encoding="utf-8")
    assert "- [-] Deploy server" in content
    assert "- [x] Deploy server" not in content   # not recorded as done
    assert "- [ ] Write tests" in content         # other task untouched


def test_cancel_task_no_match_returns_false(tmp_path, conn, monkeypatch):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path))
    _write_project(str(tmp_path), "SMTW", "proj.md", "---\nstatus: active\n---\n\n- [ ] Real task\n")
    ok = task_scan.cancel_task(str(tmp_path), "SMTW/proj.md", "Nonexistent task", conn)
    assert ok is False


def test_cancelled_task_dropped_by_next_scan(tmp_path, conn, monkeypatch):
    """A cancelled task shouldn't show up as still-open in the Active Tasks scan."""
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path))
    _write_project(
        str(tmp_path), "SMTW", "proj.md",
        "---\nkey: proj\nstatus: active\n---\n\n## Tasks\n\n- [ ] Deploy server\n",
    )
    assert task_scan.cancel_task(str(tmp_path), "SMTW/proj.md", "Deploy server", conn) is True

    tasks = task_scan.scan_open_tasks(str(tmp_path))
    assert tasks == []


# ---------------------------------------------------------------------------
# scan_todays_tasks
# ---------------------------------------------------------------------------

def _write_daily(root, ou, content):
    from datetime import date
    today_str = date.today().strftime("%Y-%m-%d")
    d = os.path.join(root, ou, "Daily")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{today_str}.md"), "w", encoding="utf-8") as f:
        f.write(content)


def test_scan_todays_tasks_reads_daily_file(tmp_path):
    _write_daily(
        str(tmp_path), "SMTW",
        "---\ndate: 2026-07-04 Saturday\n---\n\n## Tasks\n\n- [ ] Write report\n- [x] Done thing\n"
        "\n## Daily checklist\n\n\n## Log\n",
    )
    tasks = task_scan.scan_todays_tasks(str(tmp_path))
    assert len(tasks) == 1
    assert tasks[0]["text"] == "Write report"
    assert tasks[0]["ou"] == "SMTW"


def test_scan_todays_tasks_ignores_open_tasks_outside_tasks_section(tmp_path):
    _write_daily(
        str(tmp_path), "SMTW",
        "## Tasks\n\n- [ ] Real task\n\n## Daily checklist\n\n- [ ] Not a today-task\n",
    )
    tasks = task_scan.scan_todays_tasks(str(tmp_path))
    assert len(tasks) == 1
    assert tasks[0]["text"] == "Real task"


def test_scan_todays_tasks_skips_ou_with_no_daily_file(tmp_path):
    # SMTW has no Daily/ at all — should just contribute nothing, not error.
    os.makedirs(os.path.join(str(tmp_path), "SMTW"), exist_ok=True)
    assert task_scan.scan_todays_tasks(str(tmp_path)) == []


def test_scan_todays_tasks_does_not_pull_from_project_files(tmp_path):
    # A project file's open tasks must NOT leak into the curated today view.
    _write_project(str(tmp_path), "SMTW", "proj.md", "---\nstatus: active\n---\n\n- [ ] Backlog item\n")
    assert task_scan.scan_todays_tasks(str(tmp_path)) == []


def test_scan_todays_tasks_strips_tags_into_separate_fields(tmp_path):
    _write_daily(
        str(tmp_path), "SMTW",
        "## Tasks\n\n- [ ] Ship release priority:high due:2026-07-10 SMTW/proj.md\n",
    )
    tasks = task_scan.scan_todays_tasks(str(tmp_path))
    assert len(tasks) == 1
    t = tasks[0]
    assert t["description"] == "Ship release"
    assert t["priority"] == "high"
    assert t["project"] == "SMTW/proj.md"
    assert t["due"] == "2026-07-10"
    # raw text is untouched — toggle_task needs it verbatim
    assert t["text"] == "Ship release priority:high due:2026-07-10 SMTW/proj.md"


# ---------------------------------------------------------------------------
# add_task
# ---------------------------------------------------------------------------

def test_add_task_creates_daily_file(tmp_path, conn, monkeypatch):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path))
    _write_project(str(tmp_path), "SMTW", "proj.md", "---\nkey: proj\nstatus: active\n---\n\n## Tasks\n\n")

    ok = task_scan.add_task(str(tmp_path), "SMTW/proj.md", "Write the report", "high", "2026-08-01", conn)
    assert ok is True

    from datetime import date
    daily_path = tmp_path / "SMTW" / "Daily" / f"{date.today().strftime('%Y-%m-%d')}.md"
    content = daily_path.read_text(encoding="utf-8")
    assert "- [ ] Write the report priority:high due:2026-08-01 SMTW/proj.md" in content


def test_add_task_inserts_into_existing_daily_file(tmp_path, conn, monkeypatch):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path))
    _write_project(str(tmp_path), "SMTW", "proj.md", "---\nkey: proj\nstatus: active\n---\n\n")
    _write_daily(str(tmp_path), "SMTW", "## Tasks\n\n- [ ] Existing task\n\n## Daily checklist\n")

    ok = task_scan.add_task(str(tmp_path), "SMTW/proj.md", "New ad-hoc task", None, None, conn)
    assert ok is True

    from datetime import date
    daily_path = tmp_path / "SMTW" / "Daily" / f"{date.today().strftime('%Y-%m-%d')}.md"
    content = daily_path.read_text(encoding="utf-8")
    assert "- [ ] Existing task" in content
    assert "- [ ] New ad-hoc task SMTW/proj.md" in content


def test_add_task_rejects_nonexistent_project(tmp_path, conn, monkeypatch):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path))
    ok = task_scan.add_task(str(tmp_path), "SMTW/missing.md", "Some task", None, None, conn)
    assert ok is False


def test_add_task_rejects_path_traversal(tmp_path, conn, monkeypatch):
    import config
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(tmp_path))
    ok = task_scan.add_task(str(tmp_path), "../outside.md", "Some task", None, None, conn)
    assert ok is False
