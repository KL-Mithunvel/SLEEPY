"""
MCP Blueprint — the connector endpoint external AI apps talk to.

  POST /mcp    JSON-RPC 2.0 (MCP "Streamable HTTP", JSON responses only)
  GET  /mcp    405 — no server-initiated SSE stream is offered

The endpoint does not exist (404) unless config.MCP_ENABLED is on. Protocol in
mcp_server.py, tools in mcp_tools.py; this file only does transport concerns.
"""

import logging

from flask import Blueprint, Response, g, jsonify, request

import config
import mcp_server
import mcp_tools

logger = logging.getLogger(__name__)

mcp_bp = Blueprint("mcp", __name__)


def _db():
    from app import get_db
    return get_db()


def _scopes() -> frozenset:
    """Scopes the caller's credential grants. Replaced by token scopes in the OAuth phase."""
    return mcp_tools.ALL_SCOPES if getattr(g, "user", None) else frozenset()


def _origin_allowed() -> bool:
    origin = request.headers.get("Origin")
    return origin is None or origin in config.MCP_ALLOWED_ORIGINS


@mcp_bp.before_request
def _gate():
    if not config.MCP_ENABLED:
        return jsonify({"error": "Not found"}), 404
    if not _origin_allowed():
        return jsonify({"error": "Origin not allowed"}), 403


@mcp_bp.get("/mcp")
def mcp_get():
    return Response(status=405, headers={"Allow": "POST"})


@mcp_bp.post("/mcp")
def mcp_post():
    body = request.get_json(silent=True)
    if body is None:
        return jsonify(mcp_server.error_response(None, mcp_server.PARSE_ERROR, "Parse error")), 400

    scopes = _scopes()

    if isinstance(body, list):
        if not body:
            return jsonify(mcp_server.error_response(None, mcp_server.INVALID_REQUEST, "Empty batch")), 400
        replies = [r for r in (mcp_server.handle_message(m, scopes, _db) for m in body) if r is not None]
        if not replies:
            return Response(status=202)
        return jsonify(replies)

    reply = mcp_server.handle_message(body, scopes, _db)
    if reply is None:
        return Response(status=202)
    return jsonify(reply)
