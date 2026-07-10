"""
Tests for ai_client._load_inbox() — the filter that keeps resolved (- [x])
inbox captures from being fed into the morning briefing LLM's context forever.
Without this, a capture that's already been resolved (task checked off
elsewhere, meeting actually happened) kept resurfacing in every briefing
since inbox.md itself is never pruned, only checked off in place.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")


def test_load_inbox_strips_checked_lines(tmp_path):
    import ai_client

    (tmp_path / "inbox.md").write_text(
        "# Inbox\n\n"
        "Quick captures land here.\n"
        "- [ ] Still open — needs action\n"
        "- [x] ~~Resolved thing~~ — done\n"
        "- [X] ~~Also resolved (capital X)~~ — done\n",
        encoding="utf-8",
    )

    result = ai_client._load_inbox(str(tmp_path))
    assert "Still open" in result
    assert "Resolved thing" not in result
    assert "Also resolved" not in result


def test_load_inbox_keeps_non_checkbox_lines(tmp_path):
    import ai_client

    (tmp_path / "inbox.md").write_text(
        "# Inbox\n\n## News\n- [ ] A news bullet\n",
        encoding="utf-8",
    )

    result = ai_client._load_inbox(str(tmp_path))
    assert "# Inbox" in result
    assert "## News" in result
    assert "A news bullet" in result


def test_load_inbox_missing_file_returns_empty(tmp_path):
    import ai_client
    assert ai_client._load_inbox(str(tmp_path)) == ""
