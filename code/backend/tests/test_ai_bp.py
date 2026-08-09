"""
HTTP tests for the AI blueprint routes.
LiteLLM and ChromaDB calls are monkeypatched so tests run without live services.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# POST /api/ai/reindex
# ---------------------------------------------------------------------------

def test_reindex_enqueues_task(client):
    resp = client.post("/api/ai/reindex")
    assert resp.status_code == 202
    data = resp.get_json()
    assert "task_id" in data
    assert data["task_id"] > 0


# ---------------------------------------------------------------------------
# GET /api/ai/query
# ---------------------------------------------------------------------------

def test_query_missing_param(client):
    resp = client.get("/api/ai/query")
    assert resp.status_code == 400


def test_query_returns_chunks(client, monkeypatch):
    import md_indexer
    monkeypatch.setattr(
        md_indexer,
        "query",
        lambda text, k=5: [
            {"content": "Task A", "file_path": "SMTW/proj.md", "heading": "Tasks", "score": 0.92}
        ],
    )
    resp = client.get("/api/ai/query?q=active+tasks")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1
    assert data["items"][0]["heading"] == "Tasks"


def test_query_empty_result(client, monkeypatch):
    import md_indexer
    monkeypatch.setattr(md_indexer, "query", lambda text, k=5: [])
    resp = client.get("/api/ai/query?q=nothing")
    assert resp.status_code == 200
    assert resp.get_json()["total"] == 0


# ---------------------------------------------------------------------------
# POST /api/ai/suggest
# ---------------------------------------------------------------------------

def test_suggest_missing_query(client):
    resp = client.post("/api/ai/suggest", json={})
    assert resp.status_code == 400


def test_suggest_returns_response(client, monkeypatch):
    import ai_client
    monkeypatch.setattr(
        ai_client,
        "build_rag_context",
        lambda q, k=None: "",
    )
    monkeypatch.setattr(
        ai_client,
        "chat",
        lambda messages, **kwargs: {
            "content": "Focus on Alpha project today.",
            "model": "mock-model",
            "input_tokens": 10,
            "output_tokens": 20,
            "latency_ms": 100,
            "event_id": 1,
        },
    )
    resp = client.post("/api/ai/suggest", json={"query": "What should I work on?"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "response" in data
    assert "Alpha" in data["response"]


# ---------------------------------------------------------------------------
# POST /api/ai/edit — propose
# ---------------------------------------------------------------------------

def test_edit_propose_missing_fields(client):
    resp = client.post("/api/ai/edit", json={"file_path": "proj.md"})
    assert resp.status_code == 400


def test_edit_propose_bad_path(client, monkeypatch):
    import ai_client
    monkeypatch.setattr(ai_client, "build_rag_context", lambda q, k=None: "")
    monkeypatch.setattr(
        ai_client,
        "chat",
        lambda messages, **kwargs: {
            "content": "evil",
            "model": "m",
            "input_tokens": 1,
            "output_tokens": 1,
            "latency_ms": 1,
            "event_id": None,
        },
    )
    resp = client.post(
        "/api/ai/edit",
        json={"file_path": "../../etc/passwd", "instruction": "inject"},
    )
    assert resp.status_code == 400
    assert "traversal" in resp.get_json()["error"].lower()


def test_edit_propose_valid(client, monkeypatch, tmp_path):
    import config
    import ai_client

    data_root = str(tmp_path / "corpus")
    os.makedirs(data_root)
    monkeypatch.setattr(config, "USER_DATA_ROOT", data_root)

    monkeypatch.setattr(ai_client, "build_rag_context", lambda q, k=None: "")
    monkeypatch.setattr(
        ai_client,
        "chat",
        lambda messages, **kwargs: {
            "content": "# Project\n\n## Tasks\n- [ ] New task due:2026-07-01",
            "model": "m",
            "input_tokens": 5,
            "output_tokens": 10,
            "latency_ms": 50,
            "event_id": None,
        },
    )
    resp = client.post(
        "/api/ai/edit",
        json={"file_path": "proj.md", "instruction": "Add a task"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "event_id" in data
    assert data["is_new"] is True


# ---------------------------------------------------------------------------
# POST /api/ai/edit/<id>/confirm and /reject
# ---------------------------------------------------------------------------

def test_edit_confirm_not_found(client):
    resp = client.post("/api/ai/edit/99999/confirm")
    assert resp.status_code == 404


def test_edit_reject_idempotent(client):
    # Reject a non-existent event — should succeed silently (UPDATE 0 rows)
    resp = client.post("/api/ai/edit/99999/reject")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/ai/chat — persists full prompt/response text, not just token counts
# ---------------------------------------------------------------------------

def test_chat_persists_user_message_and_response(client, monkeypatch):
    import llm

    def fake_chat_stream(messages, *, system=None, tools=None, model=None):
        yield "Focus on "
        yield "Alpha today."
        yield llm.ChatResult(text="Focus on Alpha today.", model="mock-model",
                              input_tokens=10, output_tokens=5)

    monkeypatch.setattr(llm, "chat_stream", fake_chat_stream)

    resp = client.post(
        "/api/ai/chat",
        json={"messages": [{"role": "user", "content": "What should I focus on?"}]},
    )
    assert resp.status_code == 200
    assert "Focus on Alpha today." in resp.get_data(as_text=True)

    from app import get_db
    with client.application.app_context():
        db = get_db()
        row = db.execute(
            "SELECT user_message, response_text FROM ai_events "
            "WHERE event_type = 'ai_chat' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["user_message"] == "What should I focus on?"
    assert row["response_text"] == "Focus on Alpha today."
