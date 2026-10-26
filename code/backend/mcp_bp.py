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
import oauth_store

logger = logging.getLogger(__name__)

mcp_bp = Blueprint("mcp", __name__)


def _db():
    from app import get_db
    return get_db()


def _origin_allowed() -> bool:
    origin = request.headers.get("Origin")
    return origin is None or origin in config.MCP_ALLOWED_ORIGINS


def _unauthorized(presented: bool) -> Response:
    """401 that points the client at the OAuth discovery document (RFC 9728 / MCP auth spec)."""
    challenge = f'Bearer resource_metadata="{config.PUBLIC_BASE_URL}/.well-known/oauth-protected-resource"'
    if presented:
        challenge += ', error="invalid_token"'
    resp = jsonify({"error": "unauthorized"})
    resp.status_code = 401
    resp.headers["WWW-Authenticate"] = challenge
    return resp


@mcp_bp.before_request
def _gate():
    if not config.MCP_ENABLED:
        return jsonify({"error": "Not found"}), 404
    if not _origin_allowed():
        return jsonify({"error": "Origin not allowed"}), 403

    header = request.headers.get("Authorization", "")
    # Local development only (config refuses DEV_AUTH_BYPASS in prod): no header at
    # all means the MCP Inspector or a curl on this machine. A header, if sent, is
    # still checked for real so the OAuth path can be exercised locally.
    if config.DEV_AUTH_BYPASS and not header:
        g.mcp_scopes = mcp_tools.ALL_SCOPES
        return
    if not header.startswith("Bearer "):
        return _unauthorized(presented=False)
    # Only a connector access token opens /mcp. A web-session JWT is not one: it
    # is a broader credential and is rejected here by construction.
    grant = oauth_store.authenticate_access(_db(), header[len("Bearer "):].strip())
    if grant is None:
        return _unauthorized(presented=True)
    g.mcp_scopes = grant["scopes"]
    g.mcp_user = grant["username"]


@mcp_bp.get("/mcp")
def mcp_get():
    return Response(status=405, headers={"Allow": "POST"})


@mcp_bp.post("/mcp")
def mcp_post():
    body = request.get_json(silent=True)
    if body is None:
        return jsonify(mcp_server.error_response(None, mcp_server.PARSE_ERROR, "Parse error")), 400

    scopes = g.mcp_scopes

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
