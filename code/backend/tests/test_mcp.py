"""
MCP connector — protocol, transport gate and the read tools.
The corpus is a throwaway tmp dir; nothing touches data/klm.
"""

import json
from datetime import date

import pytest

import config
import mcp_tools


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    root = tmp_path / "klm"
    (root / "VIT" / "Daily").mkdir(parents=True)
    (root / "SMTW").mkdir()
    (root / "Personal").mkdir()
    today = date.today().strftime("%Y-%m-%d")

    (root / "VIT" / "magnet.md").write_text("---\nstatus: active\ntitle: Magnet Embedding\n---\n\n# Magnet\n- [ ] secret body task\n", encoding="utf-8")
    (root / "SMTW" / "old.md").write_text("---\nstatus: paused\n---\n# Old\n", encoding="utf-8")
    (root / "Personal" / "home.md").write_text("# Home\nno frontmatter means active\n", encoding="utf-8")
    (root / "ABOUT.md").write_text("private about text", encoding="utf-8")
    (root / "VIT" / "Daily" / f"{today}.md").write_text(
        "---\ndate: x\n---\n\n## Tasks\n\n"
        "- [ ] Write report priority:high due:2026-12-01 VIT/magnet.md\n"
        "- [x] Already done\n"
        "- [ ] 📰 Some headline https://example.com\n\n"
        "## Log\n- [ ] not a task section item\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "USER_DATA_ROOT", str(root))
    return root


@pytest.fixture()
def mcp_on(monkeypatch):
    monkeypatch.setattr(config, "MCP_ENABLED", True)


def rpc(client, method, params=None, msg_id=1, **kw):
    body = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        body["id"] = msg_id
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, **kw)


def tool_text(resp):
    result = resp.get_json()["result"]
    return result["content"][0]["text"], result["isError"]


# ---------------------------------------------------------------------------
# Transport gate
# ---------------------------------------------------------------------------

def test_endpoint_absent_when_disabled(client):
    assert config.MCP_ENABLED is False
    assert rpc(client, "ping").status_code == 404
    assert client.get("/mcp").status_code == 404


def test_get_not_allowed(client, mcp_on):
    resp = client.get("/mcp")
    assert resp.status_code == 405
    assert resp.headers["Allow"] == "POST"


def test_unlisted_origin_rejected(client, mcp_on):
    resp = rpc(client, "ping", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_listed_origin_and_no_origin_accepted(client, mcp_on):
    assert rpc(client, "ping", headers={"Origin": "https://claude.ai"}).status_code == 200
    assert rpc(client, "ping").status_code == 200


def test_bad_json_is_parse_error(client, mcp_on):
    resp = client.post("/mcp", data="{nope", content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == -32700


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

def test_initialize_echoes_supported_version(client, mcp_on):
    r = rpc(client, "initialize", {"protocolVersion": "2025-03-26"}).get_json()["result"]
    assert r["protocolVersion"] == "2025-03-26"
    assert r["capabilities"] == {"tools": {"listChanged": False}}
    assert r["serverInfo"]["name"] == "sleepy"


def test_initialize_unknown_version_falls_back_to_latest(client, mcp_on):
    r = rpc(client, "initialize", {"protocolVersion": "1999-01-01"}).get_json()["result"]
    assert r["protocolVersion"] == "2025-06-18"


def test_notification_gets_202_and_no_body(client, mcp_on):
    resp = rpc(client, "notifications/initialized", msg_id=None)
    assert resp.status_code == 202
    assert resp.data == b""


def test_unknown_method(client, mcp_on):
    assert rpc(client, "resources/list").get_json()["error"]["code"] == -32601


def test_non_jsonrpc_body_rejected(client, mcp_on):
    resp = client.post("/mcp", json={"hello": "world"})
    assert resp.get_json()["error"]["code"] == -32600


def test_batch_replies_only_to_requests(client, mcp_on):
    resp = client.post("/mcp", json=[
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ])
    assert [r["id"] for r in resp.get_json()] == [1]


def test_empty_batch_rejected(client, mcp_on):
    assert client.post("/mcp", json=[]).status_code == 400


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

def test_tools_list_is_exactly_the_read_tools(client, mcp_on):
    tools = rpc(client, "tools/list").get_json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"get_today", "list_projects"}
    for t in tools:
        assert t["annotations"]["readOnlyHint"] is True
        assert t["inputSchema"]["additionalProperties"] is False


def test_tools_hidden_without_scope():
    assert mcp_tools.list_tools(frozenset()) == []
    with pytest.raises(mcp_tools.UnknownTool):
        mcp_tools.call_tool("get_today", {}, frozenset(), lambda: None)


def test_every_tool_declares_a_known_scope():
    for t in mcp_tools.TOOLS.values():
        assert t.scope in mcp_tools.ALL_SCOPES


def test_unknown_tool_is_invalid_params(client, mcp_on):
    r = rpc(client, "tools/call", {"name": "read_file", "arguments": {"path": "ABOUT.md"}}).get_json()
    assert r["error"]["code"] == -32602


def test_no_file_or_search_tools_exist():
    forbidden = {"read_file", "write_file", "grep", "search", "move_file", "delete_file", "news", "get_briefing"}
    assert forbidden.isdisjoint(mcp_tools.TOOLS)


def test_unexpected_argument_is_tool_error(client, mcp_on, corpus):
    text, is_error = tool_text(rpc(client, "tools/call", {"name": "get_today", "arguments": {"path": "../x"}}))
    assert is_error and "unexpected" in text


def test_non_object_arguments_is_tool_error(client, mcp_on, corpus):
    _, is_error = tool_text(rpc(client, "tools/call", {"name": "get_today", "arguments": ["x"]}))
    assert is_error


def test_crashing_tool_does_not_leak_detail(client, mcp_on, monkeypatch):
    def boom(args, get_db):
        raise RuntimeError("/srv/secret/path exploded")
    monkeypatch.setitem(mcp_tools.TOOLS, "get_today", mcp_tools.Tool(
        "get_today", "t", "d", mcp_tools.SCOPE_READ, mcp_tools._NO_ARGS, boom))
    text, is_error = tool_text(rpc(client, "tools/call", {"name": "get_today"}))
    assert is_error and "secret" not in text


# ---------------------------------------------------------------------------
# get_today
# ---------------------------------------------------------------------------

def test_get_today_returns_only_open_tasks_from_todays_daily(client, mcp_on, corpus):
    text, is_error = tool_text(rpc(client, "tools/call", {"name": "get_today", "arguments": {}}))
    assert not is_error
    assert "Write report" in text
    assert "Already done" not in text           # done items excluded
    assert "not a task section item" not in text  # only the ## Tasks section
    assert "secret body task" not in text        # project backlog never leaks
    assert "private about text" not in text


def test_get_today_fields_and_news_line_is_labelled_untrusted(client, mcp_on, corpus):
    text, _ = tool_text(rpc(client, "tools/call", {"name": "get_today"}))
    assert '"priority": "high"' in text
    assert '"due": "2026-12-01"' in text
    assert '"project": "VIT/magnet.md"' in text
    news_lines = [ln for ln in text.splitlines() if "📰" in ln]
    assert news_lines and all(ln.startswith("<untrusted_web_content>") for ln in news_lines)


def test_get_today_with_no_daily_file(client, mcp_on, corpus):
    for f in (corpus / "VIT" / "Daily").iterdir():
        f.unlink()
    text, is_error = tool_text(rpc(client, "tools/call", {"name": "get_today"}))
    assert not is_error and json.loads(text)["count"] == 0


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------

def test_list_projects_active_only_titles_only(client, mcp_on, corpus):
    text, is_error = tool_text(rpc(client, "tools/call", {"name": "list_projects"}))
    assert not is_error
    data = json.loads(text)
    by_path = {p["rel_path"]: p for p in data["projects"]}
    assert set(by_path) == {"VIT/magnet.md", "Personal/home.md"}   # paused one, ABOUT.md, Daily/ excluded
    assert by_path["VIT/magnet.md"] == {"rel_path": "VIT/magnet.md", "title": "Magnet Embedding", "ou": "VIT"}
    assert "secret body task" not in text and "no frontmatter" not in text
