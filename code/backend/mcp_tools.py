"""
The MCP connector's tool surface — deliberately tiny, to-do list only.

An external AI app (Claude mobile, ...) gets exactly the tools registered here
and nothing else: no file read/write, no search over the corpus, no news feed,
no briefing (it embeds inbox text), no move/delete. Every tool is a thin wrapper over
code the web UI already uses (task_scan), so path guards, the secret scan and
the `sleepy` git commit all still apply.

    TOOLS                          name -> Tool
    list_tools(scopes)             -> MCP `tools/list` entries the caller's scopes allow
    call_tool(name, args, scopes, get_db) -> MCP `tools/call` result dict

Each tool declares the OAuth scope it needs; a token without it neither sees
the tool in the list nor can call it by name.
"""

import json
from dataclasses import dataclass
from typing import Callable

import config
import task_scan
import untrusted

SCOPE_READ = "tasks:read"
SCOPE_WRITE = "tasks:write"
ALL_SCOPES = frozenset({SCOPE_READ, SCOPE_WRITE})


class ToolError(Exception):
    """A failure the model should see and can act on (bad input, not found)."""


class UnknownTool(Exception):
    """Name not registered, or not permitted by the caller's scopes."""


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    scope: str
    input_schema: dict
    fn: Callable
    read_only: bool = True


_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _get_today(args: dict, get_db) -> dict:
    tasks = task_scan.scan_todays_tasks(config.USER_DATA_ROOT)
    return {
        "count": len(tasks),
        "tasks": [
            {
                "task": t["description"],
                "priority": t["priority"],
                "due": t["due"],
                "project": t["project"],
                "area": t["ou"],
                # Exact handle for later write tools; copy verbatim.
                "rel_path": t["rel_path"],
                "text": t["text"],
            }
            for t in tasks
        ],
    }


def _list_projects(args: dict, get_db) -> dict:
    projects = task_scan.list_active_projects(config.USER_DATA_ROOT)
    return {"count": len(projects), "projects": projects}


TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool(
        name="get_today",
        title="Today's tasks",
        description=(
            "List the user's open tasks for today (their curated daily to-do list). "
            "Returns task text, priority, due date and project. Does not include "
            "news, notes or the morning briefing."
        ),
        scope=SCOPE_READ,
        input_schema=_NO_ARGS,
        fn=_get_today,
    ),
    Tool(
        name="list_projects",
        title="List projects",
        description=(
            "List the user's active projects (name, area and `rel_path`). Use this to "
            "find the exact `rel_path` of a project before adding a task to it."
        ),
        scope=SCOPE_READ,
        input_schema=_NO_ARGS,
        fn=_list_projects,
    ),
)}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def list_tools(scopes) -> list[dict]:
    out = []
    for t in TOOLS.values():
        if t.scope not in scopes:
            continue
        out.append({
            "name": t.name,
            "title": t.title,
            "description": t.description,
            "inputSchema": t.input_schema,
            "annotations": {
                "title": t.title,
                "readOnlyHint": t.read_only,
                "destructiveHint": False,
                "openWorldHint": False,
            },
        })
    return out


def _check_args(tool: Tool, args) -> dict:
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    allowed = set(tool.input_schema.get("properties", {}))
    extra = set(args) - allowed
    if extra:
        raise ToolError(f"unexpected argument(s): {', '.join(sorted(extra))}")
    missing = [k for k in tool.input_schema.get("required", []) if k not in args]
    if missing:
        raise ToolError(f"missing required argument(s): {', '.join(missing)}")
    return args


def call_tool(name: str, args, scopes, get_db) -> dict:
    tool = TOOLS.get(name)
    if tool is None or tool.scope not in scopes:
        raise UnknownTool(name)
    try:
        result = tool.fn(_check_args(tool, args), get_db)
        text = untrusted.mark_untrusted(json.dumps(result, indent=1, ensure_ascii=False))
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
