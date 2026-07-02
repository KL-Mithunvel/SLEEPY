"""
Tests for news_watch.py's standalone-topic support (NewsWatch.md):
topic-file parsing, dormancy calculation, and the submit flow's
active-vs-dormant request shape. Anthropic client calls are monkeypatched.
"""

import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

import news_watch  # noqa: E402


# ---------------------------------------------------------------------------
# _load_topic_file
# ---------------------------------------------------------------------------

def test_load_topic_file_missing_returns_empty(tmp_path):
    assert news_watch._load_topic_file(str(tmp_path)) == []


def test_load_topic_file_parses_valid_skips_malformed(tmp_path):
    (tmp_path / "NewsWatch.md").write_text(
        "# News Watch\n\n"
        "## Soft Robotics\n"
        "- added: 2026-06-01\n\n"
        "## Bad Topic\n"
        "no added line here\n\n"
        "## Another Topic\n"
        "- added: not-a-date\n",
        encoding="utf-8",
    )
    result = news_watch._load_topic_file(str(tmp_path))
    assert len(result) == 1
    assert result[0]["topic"] == "Soft Robotics"
    assert result[0]["added"] == date(2026, 6, 1)


# ---------------------------------------------------------------------------
# _topic_is_dormant
# ---------------------------------------------------------------------------

def test_dormant_false_when_recent():
    today = date(2026, 7, 2)
    added = today - timedelta(days=5)
    assert news_watch._topic_is_dormant("X", added, [], today, dormant_days=30) is False


def test_dormant_true_when_old_and_no_feedback():
    today = date(2026, 7, 2)
    added = today - timedelta(days=60)
    assert news_watch._topic_is_dormant("X", added, [], today, dormant_days=30) is True


def test_dormant_false_when_old_but_recent_like():
    today = date(2026, 7, 2)
    added = today - timedelta(days=60)
    seen = [{
        "bullet": "- [ ] 📰 [Title](https://x.com/a) — x.com (2026-06-25) | *topic: X*",
        "feedback": "+1",
        "date": "2026-06-25",
    }]
    assert news_watch._topic_is_dormant("X", added, seen, today, dormant_days=30) is False


def test_dormant_true_when_old_and_stale_like():
    today = date(2026, 7, 2)
    added = today - timedelta(days=90)
    seen = [{
        "bullet": "- [ ] 📰 [Title](https://x.com/a) — x.com (2026-01-01) | *topic: X*",
        "feedback": "+1",
        "date": "2026-01-01",
    }]
    assert news_watch._topic_is_dormant("X", added, seen, today, dormant_days=30) is True


def test_dormant_ignores_other_topics_feedback():
    today = date(2026, 7, 2)
    added = today - timedelta(days=60)
    seen = [{
        "bullet": "- [ ] 📰 [Title](https://x.com/a) — x.com (2026-07-01) | *topic: Y*",
        "feedback": "+1",
        "date": "2026-07-01",
    }]
    assert news_watch._topic_is_dormant("X", added, seen, today, dormant_days=30) is True


# ---------------------------------------------------------------------------
# news_watch_submit_for_user — standalone topics end-to-end
# ---------------------------------------------------------------------------

@pytest.fixture()
def captured_requests(monkeypatch):
    """Monkeypatch anthropic.Anthropic; returns a list that batch-create calls append to."""
    captured: list = []

    class _FakeBatches:
        def create(self, requests):
            captured.append(requests)
            return SimpleNamespace(id="batch_123")

    class _FakeClient:
        def __init__(self, *a, **kw):
            self.beta = SimpleNamespace(messages=SimpleNamespace(batches=_FakeBatches()))

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", _FakeClient)
    return captured


def test_submit_builds_active_and_dormant_requests(tmp_path, monkeypatch, captured_requests):
    import config
    monkeypatch.setattr(config, "NEWS_RUN_ALL", True)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test")

    today = date.today()
    old_date = (today - timedelta(days=60)).isoformat()
    recent_date = today.isoformat()

    (tmp_path / "NewsWatch.md").write_text(
        "# News Watch\n\n"
        f"## Fresh Topic\n- added: {recent_date}\n\n"
        f"## Old Topic\n- added: {old_date}\n",
        encoding="utf-8",
    )

    result = news_watch.news_watch_submit_for_user(str(tmp_path))

    assert result["status"] == "submitted"
    assert result["request_count"] == 2
    assert len(captured_requests) == 1

    sent = {r["custom_id"]: r for r in captured_requests[0]}
    assert set(sent.keys()) == {"newswatch__fresh-topic", "newswatch__old-topic"}

    fresh_req = sent["newswatch__fresh-topic"]
    old_req = sent["newswatch__old-topic"]

    fresh_system_text = " ".join(b["text"] for b in fresh_req["params"]["system"])
    old_system_text = " ".join(b["text"] for b in old_req["params"]["system"])

    assert news_watch._DORMANT_SUFFIX not in fresh_system_text
    assert news_watch._DORMANT_SUFFIX in old_system_text

    fresh_ask = fresh_req["params"]["messages"][0]["content"][0]["text"]
    old_ask = old_req["params"]["messages"][0]["content"][0]["text"]
    assert "up to 3" in fresh_ask
    assert "up to 1" in old_ask


# ---------------------------------------------------------------------------
# mark_clicked
# ---------------------------------------------------------------------------

def test_mark_clicked_updates_matching_entry(tmp_path):
    bullet = "- [ ] 📰 [Title](https://x.com/a) — x.com (2026-07-01)"
    news_watch._save_news_seen(str(tmp_path), [
        {"bullet": bullet, "date": "2026-07-01", "feedback": None, "clicked": False, "shown_count": 1},
    ])
    ok = news_watch.mark_clicked(str(tmp_path), bullet)
    assert ok is True

    seen = news_watch._load_news_seen(str(tmp_path))
    assert seen[0]["clicked"] is True


def test_mark_clicked_no_match_returns_false(tmp_path):
    news_watch._save_news_seen(str(tmp_path), [])
    assert news_watch.mark_clicked(str(tmp_path), "- [ ] not there") is False


# ---------------------------------------------------------------------------
# _upsert_seen_entries
# ---------------------------------------------------------------------------

def test_upsert_appends_new_entry():
    seen = []
    bullet = "- [ ] 📰 [Title](https://x.com/a) — x.com (2026-07-01)"
    news_watch._upsert_seen_entries(seen, [bullet], "2026-07-01")
    assert len(seen) == 1
    assert seen[0]["shown_count"] == 1
    assert seen[0]["clicked"] is False


def test_upsert_bumps_shown_count_for_resurfaced_item():
    bullet = "- [ ] 📰 [Title](https://x.com/a) — x.com (2026-07-01)"
    seen = [{"bullet": bullet, "date": "2026-07-01", "feedback": "+1", "clicked": False, "shown_count": 1}]
    news_watch._upsert_seen_entries(seen, [bullet], "2026-07-05")
    assert len(seen) == 1  # no duplicate row
    assert seen[0]["shown_count"] == 2
    assert seen[0]["feedback"] == "+1"  # preserved


# ---------------------------------------------------------------------------
# news_watch_finalize_for_user — resurface-then-exclude dedup
# ---------------------------------------------------------------------------

class _FakeFinalizeBatches:
    def __init__(self, texts_by_custom_id):
        self._texts = texts_by_custom_id

    def retrieve(self, batch_id):
        return SimpleNamespace(processing_status="ended")

    def results(self, batch_id):
        out = []
        for cid, text in self._texts.items():
            message = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])
            out.append(SimpleNamespace(custom_id=cid, result=SimpleNamespace(type="succeeded", message=message)))
        return out


BULLET_TEXT = (
    "- [ ] 📰 [Soft Robot Breakthrough](https://x.com/soft-robot) — x.com (2026-07-01) "
    "| *topic: soft robotics*"
)


def _finalize_with_fake_batch(tmp_path, monkeypatch, text):
    import config
    import anthropic

    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(news_watch, "_llm_dedup", lambda client, bullets, seen: bullets)

    class _FakeClient:
        def __init__(self, *a, **kw):
            self.beta = SimpleNamespace(messages=SimpleNamespace(
                batches=_FakeFinalizeBatches({"proj__t0": text})
            ))
    monkeypatch.setattr(anthropic, "Anthropic", _FakeClient)

    news_watch._save_batch_state(str(tmp_path), {
        "job_id": "job1", "batch_id": "batch1", "custom_id_map": {
            "proj__t0": {"project_key": "proj", "topic": "soft robotics"},
        },
        "finalized": False,
    })
    return news_watch.news_watch_finalize_for_user(str(tmp_path))


def test_unclicked_item_resurfaces_until_reshow_cap(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "NEWS_MAX_RESHOW", 2)

    # First finalize: brand new item, shown_count becomes 1.
    result1 = _finalize_with_fake_batch(tmp_path, monkeypatch, BULLET_TEXT)
    assert result1["status"] == "complete"
    assert result1["appended"] == 1
    seen = news_watch._load_news_seen(str(tmp_path))
    assert len(seen) == 1
    assert seen[0]["shown_count"] == 1

    # Second finalize: same story resurfaces (not permanently excluded yet), shown_count -> 2.
    result2 = _finalize_with_fake_batch(tmp_path, monkeypatch, BULLET_TEXT)
    assert result2["appended"] == 1
    seen = news_watch._load_news_seen(str(tmp_path))
    assert len(seen) == 1  # still one entry, not duplicated
    assert seen[0]["shown_count"] == 2

    # Third finalize: shown_count now at NEWS_MAX_RESHOW (2) -> permanently excluded.
    result3 = _finalize_with_fake_batch(tmp_path, monkeypatch, BULLET_TEXT)
    assert result3["appended"] == 0


def test_clicked_item_excluded_immediately(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "NEWS_MAX_RESHOW", 5)

    _finalize_with_fake_batch(tmp_path, monkeypatch, BULLET_TEXT)
    news_watch.mark_clicked(str(tmp_path), BULLET_TEXT)

    result = _finalize_with_fake_batch(tmp_path, monkeypatch, BULLET_TEXT)
    assert result["appended"] == 0  # clicked -> excluded even though shown_count is well under cap
