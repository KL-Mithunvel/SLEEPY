"""
Model Context Protocol server core — JSON-RPC 2.0 over "Streamable HTTP",
stateless and JSON-only (no SSE, no sessions).

Hand-rolled on purpose: the surface we serve is initialize / ping / tools/list /
tools/call, which is ~100 lines, and the official SDK is async/ASGI — a poor
fit for a single-worker Flask+gunicorn app that also owns a SQLite connection
per request. Stateless means nothing to expire, nothing to leak between
callers, and a restart loses nothing.

    handle_message(msg, scopes, get_db) -> response dict, or None for a
                                           notification / client response
"""

import logging

import mcp_tools

logger = logging.getLogger(__name__)

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "sleepy", "title": "SLEEPY to-do list", "version": "1.0.0"}

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def error_response(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _ok(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _initialize(params: dict) -> dict:
    requested = params.get("protocolVersion")
    version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
        "instructions": (
            "Access to the user's to-do list only: read today's tasks and list "
            "projects. There is no access to notes, news or files."
        ),
    }


def handle_message(msg, scopes, get_db):
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return error_response(None, INVALID_REQUEST, "Invalid JSON-RPC message")

    method = msg.get("method")
    if method is None:
        return None  # a response to something we never sent — ignore
    if not isinstance(method, str):
        return error_response(msg.get("id"), INVALID_REQUEST, "Invalid method")
    if "id" not in msg:
        return None  # notification (e.g. notifications/initialized) — no reply

    msg_id = msg["id"]
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return error_response(msg_id, INVALID_PARAMS, "params must be an object")

    if method == "initialize":
        return _ok(msg_id, _initialize(params))
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": mcp_tools.list_tools(scopes)})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return error_response(msg_id, INVALID_PARAMS, "Missing tool name")
        try:
            return _ok(msg_id, mcp_tools.call_tool(name, params.get("arguments"), scopes, get_db))
        except mcp_tools.UnknownTool:
            return error_response(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
        except Exception:
            # Detail goes to the log, not to the caller.
            logger.exception("MCP tool %s failed", name)
            return _ok(msg_id, {
                "content": [{"type": "text", "text": "The tool failed unexpectedly."}],
                "isError": True,
            })

    return error_response(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
