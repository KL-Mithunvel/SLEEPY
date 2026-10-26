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

The model driving these tools may have read attacker-controlled text elsewhere
in its conversation, so write tools treat their arguments as hostile: text is
collapsed to one line, tag syntax the corpus parsers key off is refused, a
project must be a real active one, toggling is limited to lines currently on
today's list, and writes are rate-limited.
"""

import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import date
from typing import Callable

import config
import task_scan
import untrusted

SCOPE_READ = "tasks:read"
SCOPE_WRITE = "tasks:write"
ALL_SCOPES = frozenset({SCOPE_READ, SCOPE_WRITE})

MAX_TASK_LEN = 300
MAX_CAPTURE_LEN = 500
WRITE_LIMIT = 60            # writes allowed ...
WRITE_WINDOW_SECONDS = 3600  # ... per rolling hour, across all connector callers


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
    idempotent: bool = True


_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}


# ---------------------------------------------------------------------------
# Write budget — a prompt-injected loop must not be able to flood the corpus
# ---------------------------------------------------------------------------

_write_times: deque = deque()
_write_lock = threading.Lock()


def _spend_write() -> None:
    now = time.monotonic()
    with _write_lock:
        while _write_times and now - _write_times[0] > WRITE_WINDOW_SECONDS:
            _write_times.popleft()
        if len(_write_times) >= WRITE_LIMIT:
            raise ToolError("Write limit reached for this hour. Try again later.")
        _write_times.append(now)


# ---------------------------------------------------------------------------
# Argument handling
# ---------------------------------------------------------------------------

_HOSTILE_SYNTAX_RE = re.compile(r"\^p:|\bpriority:|\bdue:|\S+/\S+\.md", re.IGNORECASE)


def _clean_text(raw: str, what: str, *, forbid_tags: bool) -> str:
    """One line, no comment markers, non-empty; optionally no parser tag syntax."""
    text = task_scan.single_line(raw).replace("<!--", "").replace("-->", "").strip()
    if not text:
        raise ToolError(f"{what} must not be empty")
    if forbid_tags and _HOSTILE_SYNTAX_RE.search(text):
        raise ToolError(
            f"{what} must be plain text: don't include 'priority:', 'due:', '^p:' or file paths "
            "in it — use the priority / due / project arguments instead"
        )
    return text


def _check_args(tool: Tool, args) -> dict:
    """Validate against the tool's own JSON schema (string/enum/pattern/length only)."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    props = tool.input_schema.get("properties", {})
    extra = set(args) - set(props)
    if extra:
        raise ToolError(f"unexpected argument(s): {', '.join(sorted(extra))}")
    missing = [k for k in tool.input_schema.get("required", []) if k not in args]
    if missing:
        raise ToolError(f"missing required argument(s): {', '.join(missing)}")
    for key, value in args.items():
        spec = props[key]
        if not isinstance(value, str):
            raise ToolError(f"{key} must be a string")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolError(f"{key} must be one of: {', '.join(spec['enum'])}")
        if len(value) > spec.get("maxLength", 10_000):
            raise ToolError(f"{key} is too long (max {spec['maxLength']} characters)")
        if "pattern" in spec and not re.fullmatch(spec["pattern"], value):
            raise ToolError(f"{key} has the wrong format")
    return args


# ---------------------------------------------------------------------------
# Read tools
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
                # Exact handle for toggle_task; copy both verbatim.
                "rel_path": t["rel_path"],
                "exact_text": t["text"],
            }
            for t in tasks
        ],
    }


def _list_projects(args: dict, get_db) -> dict:
    projects = task_scan.list_active_projects(config.USER_DATA_ROOT)
    return {"count": len(projects), "projects": projects}


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------

def _add_task(args: dict, get_db) -> dict:
    text = _clean_text(args["task"], "task", forbid_tags=True)
    project = args["project"].strip()
    priority = args.get("priority") or None
    due = args.get("due") or None

    if due:
        try:
            date.fromisoformat(due)
        except ValueError:
            raise ToolError("due is not a real date (use YYYY-MM-DD)")

    if project not in {p["rel_path"] for p in task_scan.list_active_projects(config.USER_DATA_ROOT)}:
        raise ToolError("Unknown project. Call list_projects and use one of its rel_path values.")

    # A model that times out and retries must not double-add.
    for t in task_scan.scan_todays_tasks(config.USER_DATA_ROOT):
        if t["project"] == project and t["description"].casefold() == text.casefold():
            return {"added": False, "note": "That task is already on today's list.", "task": text}

    _spend_write()
    if not task_scan.add_task(config.USER_DATA_ROOT, project, text, priority, due, get_db()):
        raise ToolError("Could not add the task.")
    return {"added": True, "task": text, "project": project, "priority": priority, "due": due}


def _capture(args: dict, get_db) -> dict:
    text = _clean_text(args["text"], "text", forbid_tags=False)
    _spend_write()
    try:
        task_scan.capture_to_inbox(config.USER_DATA_ROOT, text, get_db())
    except ValueError:
        raise ToolError("Could not save that note.")
    return {"captured": True, "text": text}


def _toggle_task(args: dict, get_db) -> dict:
    rel_path, exact_text = args["rel_path"], args["exact_text"]
    # Only a line that is on today's list right now — never an arbitrary file/line.
    on_list = any(
        t["rel_path"] == rel_path and t["text"] == exact_text
        for t in task_scan.scan_todays_tasks(config.USER_DATA_ROOT)
    )
    if not on_list:
        raise ToolError("That task isn't on today's open list. Call get_today and copy rel_path and exact_text exactly.")
    _spend_write()
    if not task_scan.toggle_task(config.USER_DATA_ROOT, rel_path, exact_text, get_db()):
        raise ToolError("Could not mark that task done — it may have changed. Call get_today and try again.")
    return {"done": True, "task": exact_text}


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
    Tool(
        name="add_task",
        title="Add a task",
        description=(
            "Add a task to today's to-do list, filed under one of the user's active projects. "
            "Call list_projects first and pass a project's `rel_path` exactly. The task text must be "
            "plain text (no 'priority:', 'due:' or file paths) — use the priority and due arguments. "
            "Adding the same task twice is harmless."
        ),
        scope=SCOPE_WRITE,
        input_schema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "maxLength": MAX_TASK_LEN, "description": "What needs doing."},
                "project": {"type": "string", "maxLength": 200, "description": "A project rel_path from list_projects, e.g. VIT/magnet.md"},
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                "due": {"type": "string", "pattern": r"\d{4}-\d{2}-\d{2}", "maxLength": 10, "description": "YYYY-MM-DD"},
            },
            "required": ["task", "project"],
            "additionalProperties": False,
        },
        fn=_add_task,
        read_only=False,
    ),
    Tool(
        name="capture",
        title="Quick capture",
        description=(
            "Save a quick note or idea to the user's inbox for later triage. Use this when the user "
            "says to jot something down but it isn't clearly a task for a specific project."
        ),
        scope=SCOPE_WRITE,
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": MAX_CAPTURE_LEN}},
            "required": ["text"],
            "additionalProperties": False,
        },
        fn=_capture,
        read_only=False,
        idempotent=False,
    ),
    Tool(
        name="toggle_task",
        title="Mark a task done",
        description=(
            "Mark one of today's open tasks as done. Call get_today first and pass that task's "
            "`rel_path` and `exact_text` exactly as returned."
        ),
        scope=SCOPE_WRITE,
        input_schema={
            "type": "object",
            "properties": {
                "rel_path": {"type": "string", "maxLength": 200},
                "exact_text": {"type": "string", "maxLength": 1200},
            },
            "required": ["rel_path", "exact_text"],
            "additionalProperties": False,
        },
        fn=_toggle_task,
        read_only=False,
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
                "idempotentHint": t.idempotent,
                "openWorldHint": False,
            },
        })
    return out


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
