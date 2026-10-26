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


@pytest.fixture(autouse=True)
def _fresh_write_budget():
    mcp_tools._write_times.clear()
    yield
    mcp_tools._write_times.clear()


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


def test_oversized_batch_rejected_before_any_work(client, mcp_on, monkeypatch):
    calls = []
    monkeypatch.setattr("mcp_server.handle_message", lambda *a: calls.append(a))
    batch = [{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(11)]
    assert client.post("/mcp", json=batch).status_code == 400
    assert calls == []


def test_batch_at_the_cap_is_served(client, mcp_on):
    ok = [{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(10)]
    assert len(client.post("/mcp", json=ok).get_json()) == 10


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

def test_tools_list_is_exactly_the_five_todo_tools(client, mcp_on):
    tools = rpc(client, "tools/list").get_json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"get_today", "list_projects", "add_task", "capture", "toggle_task"}
    for t in tools:
        assert t["inputSchema"]["additionalProperties"] is False
        assert t["annotations"]["destructiveHint"] is False
        assert t["annotations"]["readOnlyHint"] is (t["name"] in {"get_today", "list_projects"})


def test_read_scope_alone_sees_only_read_tools():
    names = {t["name"] for t in mcp_tools.list_tools(frozenset({mcp_tools.SCOPE_READ}))}
    assert names == {"get_today", "list_projects"}
    with pytest.raises(mcp_tools.UnknownTool):
        mcp_tools.call_tool("add_task", {"task": "x", "project": "VIT/magnet.md"},
                            frozenset({mcp_tools.SCOPE_READ}), lambda: None)


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


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------

def call(client, name, **arguments):
    return tool_text(rpc(client, "tools/call", {"name": name, "arguments": arguments}))


def daily_text(corpus):
    return (corpus / "VIT" / "Daily" / f"{date.today():%Y-%m-%d}.md").read_text(encoding="utf-8")


def test_add_task_lands_in_todays_daily_file(client, mcp_on, corpus):
    text, is_error = call(client, "add_task", task="Call the printer", project="VIT/magnet.md",
                          priority="high", due="2026-12-24")
    assert not is_error and json.loads(text)["added"] is True
    assert "- [ ] Call the printer priority:high due:2026-12-24 VIT/magnet.md" in daily_text(corpus)
    # and the web UI's own scan sees it
    assert "Call the printer" in call(client, "get_today")[0]


def test_add_task_twice_is_idempotent(client, mcp_on, corpus):
    call(client, "add_task", task="Renew domain", project="VIT/magnet.md")
    text, is_error = call(client, "add_task", task="renew DOMAIN", project="VIT/magnet.md")
    assert not is_error and json.loads(text)["added"] is False
    assert daily_text(corpus).lower().count("renew domain") == 1


@pytest.mark.parametrize("project", [
    "SMTW/old.md",              # paused project
    "ABOUT.md",                 # not a project
    "VIT/Daily/x.md",           # generated subfolder
    "../outside.md",
    "VIT/nope.md",
])
def test_add_task_rejects_non_active_or_unlisted_project(client, mcp_on, corpus, project):
    text, is_error = call(client, "add_task", task="Sneaky", project=project)
    assert is_error and "list_projects" in text
    assert "Sneaky" not in daily_text(corpus)


@pytest.mark.parametrize("task", [
    "do it priority:high",
    "x due:2026-01-01",
    "link ^p:deadbeef",
    "file Personal/home.md",
    "   ",
])
def test_add_task_rejects_parser_syntax_and_empty(client, mcp_on, corpus, task):
    _, is_error = call(client, "add_task", task=task, project="VIT/magnet.md")
    assert is_error


def test_add_task_newlines_cannot_inject_a_heading(client, mcp_on, corpus):
    call(client, "add_task", task="innocent\n\n## Evil heading\n- [ ] second line", project="VIT/magnet.md")
    body = daily_text(corpus)
    assert "\n## Evil heading" not in body
    assert body.count("innocent ## Evil heading - [ ] second line") == 1


def test_add_task_strips_comment_markers(client, mcp_on, corpus):
    call(client, "add_task", task="hide <!-- this --> well", project="VIT/magnet.md")
    body = daily_text(corpus)
    assert "<!--" not in body.split("## Tasks", 1)[1].split("## Log")[0]


@pytest.mark.parametrize("bad", [
    {"priority": "urgent"},
    {"due": "tomorrow"},
    {"due": "2026-13-45"},
    {"task": "x" * 301},
    {"task": 5},
])
def test_add_task_validates_arguments(client, mcp_on, corpus, bad):
    args = {"task": "ok", "project": "VIT/magnet.md", **bad}
    _, is_error = call(client, "add_task", **args)
    assert is_error


def test_capture_appends_to_inbox(client, mcp_on, corpus):
    text, is_error = call(client, "capture", text="Try the new\nresin printer")
    assert not is_error
    inbox = (corpus / "inbox.md").read_text(encoding="utf-8")
    assert "- [ ] Try the new resin printer  <!-- captured " in inbox


def test_capture_strips_comment_markers_and_length_limited(client, mcp_on, corpus):
    call(client, "capture", text="a --> b <!-- c")
    line = [ln for ln in (corpus / "inbox.md").read_text(encoding="utf-8").splitlines() if ln.startswith("- [ ] a")][0]
    assert line.startswith("- [ ] a  b  c  <!-- captured ")   # only our own comment remains
    assert line.count("<!--") == 1 and line.count("-->") == 1
    _, is_error = call(client, "capture", text="x" * 501)
    assert is_error


def test_toggle_marks_done_only_for_a_line_on_todays_list(client, mcp_on, corpus):
    rel = f"VIT/Daily/{date.today():%Y-%m-%d}.md"
    exact = "Write report priority:high due:2026-12-01 VIT/magnet.md"
    text, is_error = call(client, "toggle_task", rel_path=rel, exact_text=exact)
    assert not is_error and json.loads(text)["done"] is True
    assert f"- [x] {exact}" in daily_text(corpus)
    assert "Write report" not in call(client, "get_today")[0]


def test_toggle_refuses_a_project_backlog_line(client, mcp_on, corpus):
    _, is_error = call(client, "toggle_task", rel_path="VIT/magnet.md", exact_text="secret body task")
    assert is_error
    assert "- [ ] secret body task" in (corpus / "VIT" / "magnet.md").read_text(encoding="utf-8")


def test_toggle_refuses_arbitrary_paths(client, mcp_on, corpus):
    for rel in ("../../etc/passwd", "ABOUT.md", "inbox.md", "VIT/Daily/../magnet.md"):
        _, is_error = call(client, "toggle_task", rel_path=rel, exact_text="anything")
        assert is_error


def test_write_budget_stops_a_flood(client, mcp_on, corpus, monkeypatch):
    monkeypatch.setattr(mcp_tools, "WRITE_LIMIT", 3)
    results = [call(client, "capture", text=f"note {i}") for i in range(5)]
    assert [e for _, e in results] == [False, False, False, True, True]
    assert "limit" in results[-1][0].lower()


def test_failed_validation_does_not_spend_write_budget(client, mcp_on, corpus, monkeypatch):
    monkeypatch.setattr(mcp_tools, "WRITE_LIMIT", 1)
    call(client, "add_task", task="bad priority:high", project="VIT/magnet.md")
    call(client, "add_task", task="ok", project="VIT/nope.md")
    _, is_error = call(client, "capture", text="still allowed")
    assert not is_error


def test_writes_are_committed_by_sleepy(client, mcp_on, corpus):
    import git
    call(client, "capture", text="authored check")
    assert git.Repo(str(corpus)).head.commit.author.email == "sleepy@smtw.in"
