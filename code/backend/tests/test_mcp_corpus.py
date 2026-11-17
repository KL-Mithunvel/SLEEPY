"""
MCP connector — notes access (corpus:read) and staged proposals (corpus:propose),
plus the grill-me instructions/prompt. Proposals are STAGED only: nothing here
may change a file, and the connector must have no way to apply one.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import config  # noqa: E402
import mcp_server  # noqa: E402
import mcp_tools  # noqa: E402
from test_mcp import corpus, mcp_on, rpc, tool_text, _fresh_write_budget  # noqa: F401 (fixtures)


def call(client, name, **args):
    return tool_text(rpc(client, "tools/call", {"name": name, "arguments": args}))


READ_TOOLS = {"list_files", "read_file", "grep", "search_corpus"}
PROPOSE_TOOLS = {"propose_write", "propose_move", "propose_delete"}


# --- scopes ---------------------------------------------------------------

def test_corpus_tools_need_their_own_scopes():
    only_tasks = {t["name"] for t in mcp_tools.list_tools({"tasks:read", "tasks:write"})}
    assert only_tasks.isdisjoint(READ_TOOLS | PROPOSE_TOOLS)
    read = {t["name"] for t in mcp_tools.list_tools({"corpus:read"})}
    assert read == READ_TOOLS
    prop = {t["name"] for t in mcp_tools.list_tools({"corpus:propose"})}
    assert prop == PROPOSE_TOOLS


def test_call_without_scope_is_unknown_tool():
    with pytest.raises(mcp_tools.UnknownTool):
        mcp_tools.call_tool("read_file", {"path": "ABOUT.md"}, {"tasks:read"}, lambda: None)
    with pytest.raises(mcp_tools.UnknownTool):
        mcp_tools.call_tool("propose_delete", {"path": "ABOUT.md", "summary": "x"}, {"corpus:read"}, lambda: None)


def test_connector_cannot_apply_anything():
    names = set(mcp_tools.TOOLS)
    assert names.isdisjoint({"write_file", "move_file", "delete_file", "apply_edit", "confirm_edit",
                             "send_email", "news", "get_briefing"})


# --- reads ----------------------------------------------------------------

def test_read_and_list(client, mcp_on, corpus):
    text, err = call(client, "read_file", path="ABOUT.md")
    assert not err and "private about text" in text
    text, err = call(client, "list_files")
    assert not err and "VIT/magnet.md" in text


def test_read_refuses_escapes_and_internal_paths(client, mcp_on, corpus):
    for bad in ("../secrets_app.py", "..\\..\\x.md", "/etc/passwd", "db/sqlite/pma.db", ".git/config", "ABOUT.txt"):
        _, err = call(client, "read_file", path=bad)
        assert err, bad


def test_grep_finds_text(client, mcp_on, corpus):
    text, err = call(client, "grep", pattern="secret body")
    assert not err and "VIT/magnet.md" in text


def test_grep_rejects_runaway_pattern(client, mcp_on, corpus):
    _, err = call(client, "grep", pattern="a" * 201)
    assert err


# --- staged proposals -----------------------------------------------------

def test_propose_write_stages_and_does_not_touch_the_file(client, mcp_on, corpus):
    before = (corpus / "ABOUT.md").read_text(encoding="utf-8")
    text, err = call(client, "propose_write", path="ABOUT.md", content="changed\n", summary="tidy")
    assert not err
    out = json.loads(text)
    assert out["staged"] is True and out["event_id"] > 0 and "NOT applied" in out["next_step"]
    assert (corpus / "ABOUT.md").read_text(encoding="utf-8") == before


def test_proposal_is_listed_as_pending_with_connector_warning(client, mcp_on, corpus):
    call(client, "propose_write", path="SMTW/new-note.md", content="# New\n", summary="add a note")
    pending = client.get("/api/ai/pending").get_json()["pending"]
    card = next(p for p in pending if p.get("rel_path") == "SMTW/new-note.md")
    assert card["summary"] == "add a note" and card["is_new"] is True
    assert "connected AI app" in card["warnings"][0]
    assert not (corpus / "SMTW" / "new-note.md").exists()


def test_propose_write_blocks_secrets_and_bad_paths(client, mcp_on, corpus):
    _, err = call(client, "propose_write", path="ABOUT.md",
                  content="key: sk-ant-api03-" + "A" * 40 + "\n", summary="x")
    assert err
    for bad in ("../x.md", "db/x.md", "notes.txt"):
        _, err = call(client, "propose_write", path=bad, content="x", summary="x")
        assert err, bad


def test_propose_delete_and_move_are_staged_only(client, mcp_on, corpus):
    text, err = call(client, "propose_delete", path="Personal/home.md", summary="old")
    assert not err and json.loads(text)["op"] == "delete"
    text, err = call(client, "propose_move", src_path="SMTW/old.md", dst_path="VIT/old.md", summary="wrong ou")
    assert not err and json.loads(text)["op"] == "move"
    assert (corpus / "Personal" / "home.md").exists() and (corpus / "SMTW" / "old.md").exists()


def test_proposals_count_against_the_write_budget(client, mcp_on, corpus, monkeypatch):
    monkeypatch.setattr(mcp_tools, "WRITE_LIMIT", 2)
    for i in range(2):
        _, err = call(client, "propose_write", path=f"SMTW/n{i}.md", content="x\n", summary="s")
        assert not err
    _, err = call(client, "propose_write", path="SMTW/n9.md", content="x\n", summary="s")
    assert err


# --- grill-me -------------------------------------------------------------

def test_initialize_carries_the_ask_dont_guess_rule(client, mcp_on):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18"}).get_json()["result"]
    assert "Ask, don't guess" in r["instructions"]
    assert "prompts" in r["capabilities"]


def test_instructions_only_describe_granted_scopes():
    text = mcp_server._instructions({"tasks:read"})
    assert "PROPOSE" not in text and "read today's tasks" in text


def test_grill_me_prompt(client, mcp_on):
    names = [p["name"] for p in rpc(client, "prompts/list").get_json()["result"]["prompts"]]
    assert names == ["grill_me"]
    got = rpc(client, "prompts/get", {"name": "grill_me", "arguments": {"topic": "weather station"}}).get_json()
    text = got["result"]["messages"][0]["content"]["text"]
    assert "weather station" in text and "ONE question at a time" in text
    assert rpc(client, "prompts/get", {"name": "nope"}).get_json()["error"]["code"] == -32602
