"""
Grill-me: the `ask_user` chat tool. It only validates and records a
multiple-choice question; the UI renders buttons and adds its own
"type your own answer" choice, so the model must not supply one.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

import tools_registry  # noqa: E402


def _tool(questions):
    return next(t for t in tools_registry.build_tools(None, questions=questions) if t.name == "ask_user")


def test_valid_question_is_recorded():
    asked = []
    out = _tool(asked).handler({"question": "  Which OU? ", "options": ["VIT", "SMTW", "VIT"]})
    assert asked == [{"question": "Which OU?", "options": ["VIT", "SMTW"]}]  # trimmed, de-duplicated
    assert "end your turn" in out.lower()


@pytest.mark.parametrize("inp", [
    {"question": "", "options": ["a", "b"]},
    {"question": "q", "options": ["only one"]},
    {"question": "q", "options": ["a", "A"]},                       # duplicates collapse to one
    {"question": "q", "options": ["a", "b", "c", "d", "e"]},
    {"question": "q", "options": "a, b"},
    {"question": "q" * 301, "options": ["a", "b"]},
    {"question": "q", "options": ["a", "b" * 121]},
    {"question": "q"},
])
def test_invalid_question_is_rejected_and_not_recorded(inp):
    asked = []
    out = _tool(asked).handler(inp)
    assert out.startswith("[error:")
    assert asked == []


def test_only_one_question_per_turn():
    asked = []
    tool = _tool(asked)
    tool.handler({"question": "first?", "options": ["a", "b"]})
    out = tool.handler({"question": "second?", "options": ["c", "d"]})
    assert out.startswith("[error:")
    assert [q["question"] for q in asked] == ["first?"]


def test_system_prompt_points_at_the_tool():
    import config
    text = (config.SRC_ROOT / "prompts" / "SystemPrompt.MD").read_text(encoding="utf-8")
    assert "`ask_user`" in text
