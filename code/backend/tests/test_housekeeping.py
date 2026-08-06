"""
Tests for housekeeping.py's people_bio_only checker: People.md (and
<OU>/People/<nick>.md) are meant to hold bio/contact facts only, never
task/action lines — see docs/CORPUS_SCHEMA.md "People.md — bio facts only".
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

from housekeeping import check_people_bio_only


def test_no_findings_on_pure_bio_people_md(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "People.md").write_text(
        "# People\n\n## Arun Kumar Sir\n\n"
        "- **Relationship:** Advisor / Senior contact\n"
        "- **Project:** 2 DOF Pick and Place Bot (`VIT/2dof-pick-and-place.md`)\n",
        encoding="utf-8",
    )

    findings = check_people_bio_only(str(root), "ADMIN")
    assert findings == []


def test_flags_task_line_in_root_people_md(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "People.md").write_text(
        "# People\n\n## Harini\n\n- **Relationship:** Friend\n"
        "- [ ] Meeting planned for 21-07-2026\n",
        encoding="utf-8",
    )

    findings = check_people_bio_only(str(root), "ADMIN")
    assert len(findings) == 1
    assert findings[0].location == "People.md:6"
    assert "People file" in findings[0].summary


def test_flags_task_line_in_ou_people_file(tmp_path):
    root = tmp_path / "corpus"
    people_dir = root / "VIT" / "People"
    people_dir.mkdir(parents=True)
    (people_dir / "arun.md").write_text(
        "---\nnick: ARUN\n---\n\n# Arun Kumar Sir\n\n- [ ] Follow up next week\n",
        encoding="utf-8",
    )

    findings = check_people_bio_only(str(root), "ADMIN")
    assert len(findings) == 1
    assert findings[0].location == "VIT/People/arun.md:7"


def test_ignores_prose_mentioning_todo(tmp_path):
    """Only actual checkbox lines count — running prose isn't a false positive."""
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "People.md").write_text(
        "# People\n\n## Jeegadeshwaran Sir\n\n"
        "- **Notes:** Mentor for the mini project, todo items discussed informally.\n",
        encoding="utf-8",
    )

    findings = check_people_bio_only(str(root), "ADMIN")
    assert findings == []


def test_no_people_md_no_error(tmp_path):
    root = tmp_path / "corpus_empty"
    root.mkdir()
    findings = check_people_bio_only(str(root), "ADMIN")
    assert findings == []
