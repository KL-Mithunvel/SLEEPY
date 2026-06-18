# PMA MCP Server Documentation

## Overview

PMA implements the MCP (Model Context Protocol) 2025-06-18 specification, enabling Claude.ai and other MCP-compatible clients to access the user's corpus directly. This turns PMA into an MCP server that Claude.ai can connect to.

## Protocol Details

- **Protocol version**: MCP 2025-06-18
- **Transport**: Streamable HTTP (not WebSocket or stdio)
- **JSON-RPC version**: 2.0
- **Endpoint**: `POST /mcp` (JSON-RPC), `GET /mcp` (SSE hello)

## Authentication

Two authentication methods:

### Method 1: API Key (Simple)
```
X-API-Key: <MCP_API_KEY>
```
or
```
Authorization: Bearer <MCP_API_KEY>
```
where `MCP_API_KEY` is configured in secrets_app.py.

### Method 2: OAuth 2.0 (Claude.ai Connector)
PMA implements OAuth 2.0 endpoints for Claude.ai connector support:
- `GET /.well-known/oauth-authorization-server` — discovery (public)
- `GET /.well-known/oauth-protected-resource` — resource metadata (public)
- `GET /authorize` — authorization endpoint (public, redirects to Keycloak)
- `POST /token` — token endpoint (exchanges auth code for access token)

### User-Level Gate
Even with a valid API key, the user must have MCP enabled in their settings:
```python
# User settings (stored in SQLite)
{"mcp_enabled": True}
```
Set via: `PUT /api/corpus/settings` with `{"mcp_enabled": true}`

## Endpoints

### `POST /mcp` — JSON-RPC Endpoint

Handles all MCP JSON-RPC calls. Standard JSON-RPC 2.0 envelope:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "read_file",
    "arguments": {"path": "Daily/2026-06-18.md"}
  }
}
```

Response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [{"type": "text", "text": "# Daily Log..."}]
  }
}
```

### `GET /mcp` — SSE Hello

Returns an SSE stream with server hello message. Used by clients to verify the server is alive and supports Streamable HTTP.

## MCP Tools (7 Tools)

### 1. `read_file`
Read a file from the user's MD corpus.

```json
{
  "name": "read_file",
  "description": "Read a file from the corpus",
  "inputSchema": {
    "type": "object",
    "properties": {
      "path": {
        "type": "string",
        "description": "Path relative to corpus root (md_root)"
      }
    },
    "required": ["path"]
  }
}
```

### 2. `search_corpus`
Semantic vector search of the corpus.

```json
{
  "name": "search_corpus",
  "description": "Search the corpus using semantic embeddings",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {"type": "string"},
      "ou": {"type": "string", "description": "Limit to OU (optional)"},
      "include_archive": {"type": "boolean", "default": false}
    },
    "required": ["query"]
  }
}
```

### 3. `grep`
Pattern search across corpus files.

```json
{
  "name": "grep",
  "description": "Search for a text pattern in corpus files",
  "inputSchema": {
    "type": "object",
    "properties": {
      "pattern": {"type": "string"},
      "path": {"type": "string", "description": "Subdirectory to search (optional)"}
    },
    "required": ["pattern"]
  }
}
```

### 4. `list_files`
List files in the corpus.

```json
{
  "name": "list_files",
  "description": "List files in the corpus",
  "inputSchema": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Subdirectory (optional, default: root)"}
    }
  }
}
```

### 5. `write_file`
Write content to a corpus file (creates or overwrites).

```json
{
  "name": "write_file",
  "description": "Write content to a corpus file",
  "inputSchema": {
    "type": "object",
    "properties": {
      "path": {"type": "string"},
      "content": {"type": "string"}
    },
    "required": ["path", "content"]
  }
}
```

Git commit: uses `MCP_AUTHOR = Actor("PMA Bot", "mcp@company.com")`

### 6. `apply_edit`
Apply a pma-edit SEARCH/REPLACE block.

```json
{
  "name": "apply_edit",
  "description": "Apply a pma-edit SEARCH/REPLACE block to a corpus file",
  "inputSchema": {
    "type": "object",
    "properties": {
      "edit_block": {
        "type": "string",
        "description": "Full pma-edit block including file: header and <<<<<<< SEARCH / >>>>>>> REPLACE markers"
      },
      "summary": {"type": "string", "description": "Git commit summary"}
    },
    "required": ["edit_block"]
  }
}
```

### 7. `read_src`
Read a file from code/src/ (prompts, templates, help).

```json
{
  "name": "read_src",
  "description": "Read a file from code/src/ (system resources)",
  "inputSchema": {
    "type": "object",
    "properties": {
      "path": {"type": "string"}
    },
    "required": ["path"]
  }
}
```

### 8. `list_src`
List files in code/src/ directory.

```json
{
  "name": "list_src",
  "description": "List files in code/src/",
  "inputSchema": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Subdirectory (optional)"}
    }
  }
}
```

## MCP Resources (5 Resources)

Resources are read-only context exposed to MCP clients:

### `pma://system-prompt`
- URI: `pma://system-prompt`
- Content: `code/src/prompts/SystemPrompt.MD` (the main AI system prompt)
- MIME type: `text/markdown`

### `pma://user-profile`
- URI: `pma://user-profile`
- Content: user's profile info (username, OU, settings)
- MIME type: `application/json`

### `pma://project-index`
- URI: `pma://project-index`
- Content: index of all projects in the corpus
- MIME type: `text/markdown`

### `pma://template-index`
- URI: `pma://template-index`
- Content: list of available templates in code/src/templates/
- MIME type: `text/markdown`

### `pma://skills`
- URI: `pma://skills`
- Content: all skills manifest + full content
- MIME type: `text/markdown`

## MCP Prompts

Skills are exposed as MCP prompts. Each skill becomes a prompt:
- Prompt name: skill name (e.g. `daily-review`)
- Prompt description: skill description
- Prompt content: full skill markdown body

## OAuth 2.0 Discovery Endpoints (for Claude.ai)

### `GET /.well-known/oauth-authorization-server`
Returns OAuth 2.0 authorization server metadata:
```json
{
  "issuer": "https://pma.example.com",
  "authorization_endpoint": "https://pma.example.com/authorize",
  "token_endpoint": "https://pma.example.com/token",
  "response_types_supported": ["code"],
  "code_challenge_methods_supported": ["S256"],
  "scopes_supported": ["openid", "profile"]
}
```

### `GET /.well-known/oauth-protected-resource`
Returns protected resource metadata:
```json
{
  "resource": "https://pma.example.com/mcp",
  "authorization_servers": ["https://pma.example.com"]
}
```

### `GET /authorize`
Redirects to Keycloak authorization endpoint with PKCE challenge.

### `POST /token`
Exchanges authorization code for access token via Keycloak.

## Security Notes

1. **MCP is opt-in**: `mcp_enabled` must be explicitly set to `true` in user settings
2. **API key required**: `MCP_API_KEY` must be configured
3. **Path validation**: all file paths validated (no `..`, must be within md_root)
4. **No auth bypass**: DEV_AUTH_BYPASS does not apply to MCP paths
5. **Git attribution**: MCP writes use different email (`mcp@company.com`) vs chat AI (`assistant@company.com`) for audit trail

## MCP Client Configuration (Claude.ai)

To connect Claude.ai to PMA's MCP server:

1. In Claude.ai: Settings → MCP Servers → Add Server
2. URL: `https://pma.example.com/mcp`
3. Auth method: OAuth 2.0 (Claude.ai will discover endpoints via `/.well-known/`)
4. Or: add API key header `X-API-Key: <your-mcp-api-key>`

## Future: Dedicated Keycloak Client
The TODO.md notes that MCP should have a dedicated Keycloak client (`MCP_KEYCLOAK_CLIENT_ID`) separate from the main `pma` client. Currently the OAuth flow reuses the main client.
