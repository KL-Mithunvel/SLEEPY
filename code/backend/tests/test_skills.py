"""
S5 security regression: get_skill_content()'s `name` param is LLM-controlled
(the load_skill tool) and must not allow reading files outside SKILLS_DIR.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")


def test_get_skill_content_valid_skill(monkeypatch, tmp_path):
    import config
    import skills

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "daily-review.md").write_text("---\nname: daily-review\n---\nDo the review.", encoding="utf-8")
    monkeypatch.setattr(config, "SKILLS_DIR", skills_dir)

    result = skills.get_skill_content("daily-review")
    assert result == "Do the review."


def test_get_skill_content_not_found(monkeypatch, tmp_path):
    import config
    import skills

    skills_dir = tmp_path / "skills2"
    skills_dir.mkdir()
    monkeypatch.setattr(config, "SKILLS_DIR", skills_dir)

    result = skills.get_skill_content("missing-skill")
    assert "not found" in result


def test_get_skill_content_rejects_traversal(monkeypatch, tmp_path):
    import config
    import skills

    skills_dir = tmp_path / "skills3" / "skills"
    skills_dir.mkdir(parents=True)
    secret = tmp_path / "skills3" / "People.md"
    secret.write_text("SECRET CORPUS DATA", encoding="utf-8")
    monkeypatch.setattr(config, "SKILLS_DIR", skills_dir)

    result = skills.get_skill_content("../People")
    assert "SECRET CORPUS DATA" not in result
    assert "invalid skill name" in result


def test_get_skill_content_rejects_absolute_path_style(monkeypatch, tmp_path):
    import config
    import skills

    skills_dir = tmp_path / "skills4"
    skills_dir.mkdir()
    monkeypatch.setattr(config, "SKILLS_DIR", skills_dir)

    result = skills.get_skill_content("../../../../etc/passwd")
    assert "invalid skill name" in result


def test_get_skill_content_rejects_subdirectory_escape(monkeypatch, tmp_path):
    import config
    import skills

    skills_dir = tmp_path / "skills5"
    skills_dir.mkdir()
    monkeypatch.setattr(config, "SKILLS_DIR", skills_dir)

    result = skills.get_skill_content("sub/skill")
    assert "invalid skill name" in result
