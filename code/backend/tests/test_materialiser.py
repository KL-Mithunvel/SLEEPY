"""
Tests for materialiser.py's status:inactive support on Recur files.

Before this fix, materialiser.py never read any status/active field from a
Recur file's frontmatter — setting `status: inactive` (the same vocabulary
already used on project files) had zero effect, so a finished recurring
commitment (e.g. exam prep once the exam was done) kept reappearing in
Active Tasks / Plans / Govern forever. See docs/CORPUS_SCHEMA.md.
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

import materialiser  # noqa: E402


def _write_recur(root, ou, fname, content):
    d = os.path.join(root, ou, "Recur")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, fname), "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Stage 2 — materialise_daily (daily / weekly cadence)
# ---------------------------------------------------------------------------

def test_inactive_daily_recur_is_not_added(tmp_path):
    root = str(tmp_path)
    _write_recur(
        root, "VIT", "machine-vision-study.md",
        "---\ncadence: daily\ntitle: Study Machine Vision (Exam Prep)\nstatus: inactive\n---\n",
    )
    materialiser.materialise_daily(root, today=date(2026, 8, 7))

    daily_path = tmp_path / "VIT" / "Daily" / "2026-08-07.md"
    content = daily_path.read_text(encoding="utf-8")
    assert "Machine Vision" not in content


def test_active_daily_recur_is_still_added(tmp_path):
    root = str(tmp_path)
    _write_recur(
        root, "VIT", "duolingo.md",
        "---\ncadence: daily\ntitle: Duolingo\n---\n",
    )
    materialiser.materialise_daily(root, today=date(2026, 8, 7))

    daily_path = tmp_path / "VIT" / "Daily" / "2026-08-07.md"
    content = daily_path.read_text(encoding="utf-8")
    assert "Duolingo" in content


def test_inactive_weekly_recur_is_not_added(tmp_path):
    root = str(tmp_path)
    _write_recur(
        root, "SMTW", "standup.md",
        "---\ncadence: weekly\nschedule: weekday:mon\ntitle: Team Standup\nstatus: inactive\n---\n",
    )
    materialiser.materialise_daily(root, today=date(2026, 8, 10))  # a Monday

    daily_path = tmp_path / "SMTW" / "Daily" / "2026-08-10.md"
    content = daily_path.read_text(encoding="utf-8")
    assert "Team Standup" not in content


# ---------------------------------------------------------------------------
# Stage 1 — materialise_non_daily (monthly / quarterly / yearly cadence)
# ---------------------------------------------------------------------------

def test_inactive_monthly_recur_is_not_added(tmp_path):
    root = str(tmp_path)
    _write_recur(
        root, "SMTW", "rent.md",
        "---\ncadence: monthly\nschedule: day:1\ntitle: Pay Rent\nstatus: inactive\n---\n",
    )
    materialiser.materialise_non_daily(root, today=date(2026, 8, 7))

    plans_path = tmp_path / "SMTW" / "Plans" / "2026-08.md"
    assert not plans_path.exists()


def test_active_monthly_recur_is_still_added(tmp_path):
    root = str(tmp_path)
    _write_recur(
        root, "SMTW", "rent.md",
        "---\ncadence: monthly\nschedule: day:1\ntitle: Pay Rent\n---\n",
    )
    materialiser.materialise_non_daily(root, today=date(2026, 8, 7))

    plans_path = tmp_path / "SMTW" / "Plans" / "2026-08.md"
    assert "Pay Rent" in plans_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Stage 3 — materialise_govern
# ---------------------------------------------------------------------------

def test_inactive_govern_recur_is_not_added(tmp_path):
    root = str(tmp_path)
    _write_recur(
        root, "SMTW", "review.md",
        "---\ncadence: weekly\nschedule: weekday:mon\ntitle: Code Review\n"
        "owners: [teammate]\nstatus: inactive\n---\n",
    )
    materialiser.materialise_govern(root, today=date(2026, 8, 10))  # a Monday

    govern_path = tmp_path / "SMTW" / "Govern" / "2026-08.md"
    assert not govern_path.exists()


# ---------------------------------------------------------------------------
# [-] cancelled task marker — carry-forward
# ---------------------------------------------------------------------------

def _write_daily(root, ou, date_str, content):
    d = os.path.join(root, ou, "Daily")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{date_str}.md"), "w", encoding="utf-8") as f:
        f.write(content)


def test_cancelled_task_does_not_carry_forward(tmp_path):
    root = str(tmp_path)
    _write_daily(
        root, "Personal", "2026-08-06",
        "---\ndate: 2026-08-06 Thursday\n---\n\n"
        "## Tasks\n\n- [-] Renew passport\n\n## Daily checklist\n\n## Log\n",
    )
    materialiser.materialise_daily(root, today=date(2026, 8, 7))

    content = (tmp_path / "Personal" / "Daily" / "2026-08-07.md").read_text(encoding="utf-8")
    assert "Renew passport" not in content


def test_unchecked_twin_of_cancelled_task_does_not_carry_forward(tmp_path):
    root = str(tmp_path)
    _write_daily(
        root, "Personal", "2026-08-06",
        "---\ndate: 2026-08-06 Thursday\n---\n\n"
        "## Tasks\n\n- [ ] Renew passport\n- [-] Renew passport\n\n"
        "## Daily checklist\n\n## Log\n",
    )
    materialiser.materialise_daily(root, today=date(2026, 8, 7))

    content = (tmp_path / "Personal" / "Daily" / "2026-08-07.md").read_text(encoding="utf-8")
    assert "Renew passport" not in content


def test_unrelated_unchecked_task_still_carries_forward(tmp_path):
    root = str(tmp_path)
    _write_daily(
        root, "Personal", "2026-08-06",
        "---\ndate: 2026-08-06 Thursday\n---\n\n"
        "## Tasks\n\n- [ ] Still open task\n\n## Daily checklist\n\n## Log\n",
    )
    materialiser.materialise_daily(root, today=date(2026, 8, 7))

    content = (tmp_path / "Personal" / "Daily" / "2026-08-07.md").read_text(encoding="utf-8")
    assert "Still open task" in content


# ---------------------------------------------------------------------------
# Govern carry-over
# ---------------------------------------------------------------------------

def _write_govern(root, ou, month_label, content):
    d = os.path.join(root, ou, "Govern")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{month_label}.md"), "w", encoding="utf-8") as f:
        f.write(content)


def test_govern_carries_overdue_unchecked_task(tmp_path):
    root = str(tmp_path)
    _write_govern(
        root, "SMTW", "2026-07",
        "# Govern — 2026-07\n\n## @teammate\n\n"
        "- [ ] Monthly deployment review @teammate due:Jul-30\n",
    )
    materialiser.materialise_govern(root, today=date(2026, 8, 10))

    govern_path = tmp_path / "SMTW" / "Govern" / "2026-08.md"
    content = govern_path.read_text(encoding="utf-8")
    assert "## Carry-overs" in content
    assert "Monthly deployment review" in content
    assert "*(overdue from 2026-07)*" in content


def test_govern_carry_over_is_idempotent(tmp_path):
    root = str(tmp_path)
    _write_govern(
        root, "SMTW", "2026-07",
        "# Govern — 2026-07\n\n## @teammate\n\n"
        "- [ ] Monthly deployment review @teammate due:Jul-30\n",
    )
    materialiser.materialise_govern(root, today=date(2026, 8, 10))
    materialiser.materialise_govern(root, today=date(2026, 8, 10))

    content = (tmp_path / "SMTW" / "Govern" / "2026-08.md").read_text(encoding="utf-8")
    assert content.count("Monthly deployment review") == 1


def test_govern_no_carry_over_without_previous_month_file(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "SMTW"), exist_ok=True)

    stats = materialiser.materialise_govern(root, today=date(2026, 8, 10))

    assert stats["carried_over"] == 0
    govern_path = tmp_path / "SMTW" / "Govern" / "2026-08.md"
    assert not govern_path.exists()
