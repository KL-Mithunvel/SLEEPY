"""
Tests for goal_planner.py — project deadline scanning, section replace,
digest formatting, and the end-to-end run_goal_planning flow.
LLM calls and git commits are monkeypatched / naturally no-op (no git repo in tmp_path).
"""

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

import goal_planner  # noqa: E402


# ---------------------------------------------------------------------------
# _replace_section
# ---------------------------------------------------------------------------

def test_replace_section_existing():
    content = (
        "---\nkey: x\n---\n\n"
        "# X\n\n## Plan\n\nold plan text\n\n## Notes\n\nsome notes\n"
    )
    result = goal_planner._replace_section(content, "Plan", "new plan text")
    assert "old plan text" not in result
    assert "new plan text" in result
    assert "## Notes" in result
    assert "some notes" in result


def test_replace_section_missing_inserts_before_ai_notes():
    content = "---\nkey: x\n---\n\n# X\n\n## Goal\n\nDo the thing.\n\n## AI Notes\n"
    result = goal_planner._replace_section(content, "Plan", "fresh plan")
    plan_idx = result.find("## Plan")
    notes_idx = result.find("## AI Notes")
    assert plan_idx != -1
    assert notes_idx != -1
    assert plan_idx < notes_idx
    assert "fresh plan" in result


def test_replace_section_missing_no_anchor_appends():
    content = "---\nkey: x\n---\n\n# X\n\n## Goal\n\nDo the thing.\n"
    result = goal_planner._replace_section(content, "Plan", "fresh plan")
    assert "## Plan" in result
    assert "fresh plan" in result


# ---------------------------------------------------------------------------
# _format_digest_line
# ---------------------------------------------------------------------------

def test_format_digest_line_future():
    line = goal_planner._format_digest_line("Proj", date(2026, 8, 1), 5, "write the doc")
    assert "5 days left" in line
    assert "write the doc" in line
    assert "Proj" in line


def test_format_digest_line_due_today():
    line = goal_planner._format_digest_line("Proj", date(2026, 8, 1), 0, None)
    assert "due today" in line


def test_format_digest_line_overdue():
    line = goal_planner._format_digest_line("Proj", date(2026, 8, 1), -3, None)
    assert "OVERDUE by 3 day" in line


def test_format_digest_line_singular_day():
    line = goal_planner._format_digest_line("Proj", date(2026, 8, 1), 1, None)
    assert "1 day left" in line
    assert "1 days left" not in line


# ---------------------------------------------------------------------------
# _extract_today_action
# ---------------------------------------------------------------------------

def test_extract_today_action_present():
    body = "**This week's goal:** ship it.\n**Today's next action:** write the README."
    action = goal_planner._extract_today_action(body)
    assert action == "write the README"


def test_extract_today_action_absent():
    body = "**This week's goal:** ship it."
    assert goal_planner._extract_today_action(body) is None


# ---------------------------------------------------------------------------
# run_goal_planning — scanning rules
# ---------------------------------------------------------------------------

_FAKE_PLAN = "**This week's goal:** ship it.\n**Today's next action:** write the README."


@pytest.fixture()
def fake_chat(monkeypatch):
    import ai_client
    monkeypatch.setattr(ai_client, "chat", lambda messages, **kw: {"content": _FAKE_PLAN})


def test_skips_project_without_target_date(tmp_path, fake_chat):
    proj_dir = tmp_path / "PROJ"
    proj_dir.mkdir()
    (proj_dir / "work.md").write_text(
        "---\nkey: work\nstatus: active\n---\n\n# Work\n", encoding="utf-8",
    )
    result = goal_planner.run_goal_planning(str(tmp_path))
    assert result["scanned"] == 0
    assert result["digest_lines"] == []


def test_skips_inactive_project(tmp_path, fake_chat):
    target = (date.today() + timedelta(days=10)).isoformat()
    proj_dir = tmp_path / "PROJ"
    proj_dir.mkdir()
    (proj_dir / "work.md").write_text(
        f"---\nkey: work\nstatus: paused\ntarget_date: {target}\n---\n\n# Work\n",
        encoding="utf-8",
    )
    result = goal_planner.run_goal_planning(str(tmp_path))
    assert result["scanned"] == 0


def test_skips_invalid_target_date(tmp_path, fake_chat):
    proj_dir = tmp_path / "PROJ"
    proj_dir.mkdir()
    (proj_dir / "work.md").write_text(
        "---\nkey: work\nstatus: active\ntarget_date: not-a-date\n---\n\n# Work\n",
        encoding="utf-8",
    )
    result = goal_planner.run_goal_planning(str(tmp_path))
    assert result["scanned"] == 0
    assert result["digest_lines"] == []


def test_updates_active_project_with_target_date(tmp_path, fake_chat):
    target = (date.today() + timedelta(days=5)).isoformat()
    proj_dir = tmp_path / "PROJ"
    proj_dir.mkdir()
    md_file = proj_dir / "work.md"
    md_file.write_text(
        f"---\nkey: work\nstatus: active\ntitle: Work Project\ntarget_date: {target}\n---\n\n"
        "# Work Project\n\n## Goal\n\nShip it.\n\n## AI Notes\n",
        encoding="utf-8",
    )
    result = goal_planner.run_goal_planning(str(tmp_path))
    assert result["scanned"] == 1
    assert result["updated"] == 1
    assert len(result["digest_lines"]) == 1
    assert "Work Project" in result["digest_lines"][0]
    assert "write the README" in result["digest_lines"][0]

    new_content = md_file.read_text(encoding="utf-8")
    assert "## Plan" in new_content
    assert "write the README" in new_content


def test_digest_sorted_by_urgency(tmp_path, fake_chat):
    soon = (date.today() + timedelta(days=2)).isoformat()
    later = (date.today() + timedelta(days=20)).isoformat()

    proj_dir = tmp_path / "PROJ"
    proj_dir.mkdir()
    (proj_dir / "a.md").write_text(
        f"---\nkey: a\nstatus: active\ntitle: LaterProj\ntarget_date: {later}\n---\n\n# A\n",
        encoding="utf-8",
    )
    (proj_dir / "b.md").write_text(
        f"---\nkey: b\nstatus: active\ntitle: SoonProj\ntarget_date: {soon}\n---\n\n# B\n",
        encoding="utf-8",
    )

    result = goal_planner.run_goal_planning(str(tmp_path))
    assert result["scanned"] == 2
    assert "SoonProj" in result["digest_lines"][0]
    assert "LaterProj" in result["digest_lines"][1]


def test_llm_failure_counts_as_error(tmp_path, monkeypatch):
    import ai_client
    monkeypatch.setattr(ai_client, "chat", lambda messages, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    target = (date.today() + timedelta(days=5)).isoformat()
    proj_dir = tmp_path / "PROJ"
    proj_dir.mkdir()
    (proj_dir / "work.md").write_text(
        f"---\nkey: work\nstatus: active\ntarget_date: {target}\n---\n\n# Work\n",
        encoding="utf-8",
    )
    result = goal_planner.run_goal_planning(str(tmp_path))
    assert result["scanned"] == 1
    assert result["updated"] == 0
    assert result["errors"] == 1
    assert result["digest_lines"] == []
