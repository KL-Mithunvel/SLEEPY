# PMA AI Pipeline Documentation

## Overview

PMA's AI pipeline uses Anthropic's Claude API (`claude-sonnet-4-6`) for the conversational interface. The pipeline combines RAG (Retrieval-Augmented Generation) with tools, streaming SSE responses, and a specialized markdown editing format.

## Claude API Integration (`code/backend/llm.py`)

### Model Configuration
```python
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOOL_ITERATIONS = 8
```

### Data Structures

```python
@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict      # JSON Schema for tool parameters
    handler: Callable[[dict], str]  # returns string result
```

```python
@dataclass
class ChatResult:
    text: str               # full assistant response text
    model: str              # model ID used
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int  # tokens served from prompt cache
    cache_write_tokens: int # tokens written to prompt cache
    stop_reason: str        # "end_turn" | "tool_use" | "max_tokens"
    tool_calls: list[dict]  # list of tool calls made during response
```

### Synchronous Chat (`chat()`)

Multi-turn with tool-use loop:
```python
def chat(messages, system, tools, model=DEFAULT_MODEL) -> ChatResult:
    for _ in range(MAX_TOOL_ITERATIONS):
        response = anthropic.messages.create(
            model=model,
            system=system,
            messages=messages,
            tools=[t.to_api_format() for t in tools],
        )
        if response.stop_reason != "tool_use":
            break
        # Execute tool calls, append results, loop
    return ChatResult(...)
```

### Streaming Chat (`chat_stream()`)

```python
def chat_stream(messages, system, tools, model=DEFAULT_MODEL) -> Iterator:
    # Yields: str (text delta) | dict (tool event) | ChatResult (final)
    with anthropic.messages.stream(...) as stream:
        for event in stream:
            if event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    yield event.delta.text     # str chunk
            elif event.type == "content_block_start":
                if event.content_block.type == "tool_use":
                    yield {"type": "tool_start", "name": ..., "id": ...}
        # After stream: check for tool_use, execute tools, loop
        for tool_call in tool_calls:
            result = tool.handler(tool_call.input)
            yield {"type": "tool_end", "name": ..., "id": ..., "result": result}
    yield ChatResult(...)  # final
```

### Prompt Caching

```python
def _apply_cache_control(system_blocks: list[dict]) -> list[dict]:
    # Tags the LAST system block with cache_control
    system_blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return system_blocks
```

How it works:
- Anthropic prompt caching: if the same system prompt is sent repeatedly, tokens are cached
- TTL: ~5 minutes (ephemeral)
- The system prompt is large (SystemPrompt.MD + context + skills manifest)
- Caching saves significant input token cost for repeated conversations
- `cache_read_tokens` in ChatResult shows how many tokens were served from cache
- `cache_write_tokens` shows how many tokens were written to cache (first call in window)

### Tool Result Truncation

```python
MAX_TOOL_RESULT_CHARS = 60_000

result = tool.handler(tool_input)
if len(result) > MAX_TOOL_RESULT_CHARS:
    result = result[:MAX_TOOL_RESULT_CHARS] + "\n\n[... truncated ...]"
```

## Chat Endpoint (`/api/ai/chat`)

### System Prompt Construction

The system prompt is built fresh for each request (SystemPrompt.MD is hot-reloaded from disk):

```python
system_blocks = [
    # Block 1: Main system prompt (hot-reloaded)
    {"type": "text", "text": open(PROMPTS_DIR / "SystemPrompt.MD").read()},
    
    # Block 2: Current context
    {"type": "text", "text": f"""
Current date: {today} ({weekday})
Current time: {time_ist} IST (Asia/Kolkata)
Active OU: {ou_name}
"""},
    
    # Block 3: RAG context from IndexingService
    {"type": "text", "text": f"""
## Project Context
{purpose_context}

## Project Gist
{gist_summary}

## Corpus TOC
{toc}
"""},
    
    # Block 4: Skills manifest (always short - name+description only)
    {"type": "text", "text": skills_manifest_text},
]

# Apply prompt caching to last block
system_blocks = _apply_cache_control(system_blocks)
```

### RAG (Retrieval-Augmented Generation)

```python
# Called on each /api/ai/chat request
rag_result = indexing_service.get_relevant_context(
    query=last_user_message,
    project_name=ou,
    top_k=5,
    include_archive=_wants_archive(last_user_message),
)

# Relevant chunks appended to user turn
messages[-1]["content"] = [
    {"type": "text", "text": f"## Relevant Context\n\n{rag_result['relevant_chunks']}"},
    {"type": "text", "text": original_user_message},
]
```

### Archive Detection (`_wants_archive()`)

```python
def _wants_archive(query: str) -> bool:
    # Check for retrospective/historical query patterns
    retrospective_patterns = [
        r'\b(last year|previous year|year ago|years ago)\b',
        r'\b(retrospect|recap|review|history|archive)\b',
        r'\b(2023|2024)\b',  # past year mentions
        # ... more patterns
    ]
    for pattern in retrospective_patterns:
        if re.search(pattern, query, re.IGNORECASE):
            return True
    return False
```

When `include_archive=True`, ChromaDB query does NOT filter on `archived = "false"`.

### SSE Response Format

Each SSE message: `data: <json>\n\n`

```
data: {"type": "delta", "text": "Hello"}

data: {"type": "tool_progress", "event": {"type": "tool_start", "name": "search_corpus", "id": "toolu_01..."}}

data: {"type": "tool_progress", "event": {"type": "tool_end", "name": "search_corpus", "id": "toolu_01...", "result": "..."}}

data: {"type": "done", "result": {
  "text": "Full response text",
  "model": "claude-sonnet-4-6",
  "input_tokens": 1234,
  "output_tokens": 567,
  "cache_read_tokens": 800,
  "cache_write_tokens": 0,
  "stop_reason": "end_turn",
  "tool_calls": [...],
  "actions": [...]
}}
```

The `done` event triggers frontend to:
1. Detect pma-edit blocks in `result.text`
2. Apply edits to displayed files
3. Render `result.actions` list (e.g. "Sent email to X", "Created Jira issue Y")

## LLM Tools (9 Tools Available in Chat)

### 1. `load_skill`
```json
{
  "name": "load_skill",
  "description": "Load the full content of a skill by name",
  "input_schema": {
    "type": "object",
    "properties": {
      "name": {"type": "string", "description": "Skill name (e.g. 'daily-review')"}
    },
    "required": ["name"]
  }
}
```
Handler: `skills.get_skill_content(name)` → full skill markdown body

### 2. `grep`
```json
{
  "name": "grep",
  "description": "Search for a pattern in corpus files",
  "input_schema": {
    "type": "object",
    "properties": {
      "pattern": {"type": "string"},
      "path": {"type": "string", "description": "Subdirectory to search (optional)"}
    },
    "required": ["pattern"]
  }
}
```
Handler: runs `grep -rn` in user's md_root

### 3. `read_file`
```json
{
  "name": "read_file",
  "description": "Read a file from the corpus",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Relative path from md_root"}
    },
    "required": ["path"]
  }
}
```
Handler: reads file from md_root, validates no path traversal

### 4. `read_src`
```json
{
  "name": "read_src",
  "description": "Read a file from code/src/ (prompts, templates, help)",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Relative path from code/src/"}
    },
    "required": ["path"]
  }
}
```
Handler: reads from `SRC_ROOT/<path>`, validates path

### 5. `list_src`
```json
{
  "name": "list_src",
  "description": "List files in code/src/ directory",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Subdirectory path (optional)"}
    }
  }
}
```

### 6. `list_files`
```json
{
  "name": "list_files",
  "description": "List files in the corpus",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Subdirectory to list (optional)"}
    }
  }
}
```
Handler: lists md_root or subdirectory

### 7. `search_corpus`
```json
{
  "name": "search_corpus",
  "description": "Semantic search of the corpus using vector embeddings",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {"type": "string"},
      "ou": {"type": "string", "description": "Limit to specific OU (optional)"},
      "include_archive": {"type": "boolean", "default": false}
    },
    "required": ["query"]
  }
}
```
Handler: `indexing_service.get_relevant_context(query, ou, include_archive=include_archive)`

### 8. `send_email`
```json
{
  "name": "send_email",
  "description": "Send an email",
  "input_schema": {
    "type": "object",
    "properties": {
      "to": {"type": "string", "description": "Recipient email address"},
      "subject": {"type": "string"},
      "body": {"type": "string", "description": "Email body (plain text or HTML)"}
    },
    "required": ["to", "subject", "body"]
  }
}
```
Handler: `task_queue.enqueue("email", {"to": ..., "subject": ..., "body": ...})`

### 9. `send_telegram`
```json
{
  "name": "send_telegram",
  "description": "Send a Telegram message",
  "input_schema": {
    "type": "object",
    "properties": {
      "message": {"type": "string"}
    },
    "required": ["message"]
  }
}
```
Handler: `task_queue.enqueue("telegram", {"message": ...})`

## pma-edit: Safe Markdown Editing Format

### Why Not Unified Diff?
Unified diff format (`@@` hunk headers with line counts) is error-prone when the LLM generates them — off-by-one errors in `@@` counts cause patches to fail silently or apply incorrectly.

### pma-edit Block Format
```
```pma-edit
file: <path relative to md_root>
<<<<<<< SEARCH
<exact content to find>
=======
<new content to replace with>
>>>>>>> REPLACE
```
```

### Multiple Blocks in One Response
The LLM can emit multiple pma-edit blocks in a single response. All blocks are applied atomically: if any block fails, ALL changes are rolled back.

### Validation Rules
1. `file:` path must not contain `..` (no directory traversal)
2. `file:` path must resolve within `md_root`
3. SEARCH text must match EXACTLY once (case-sensitive, whitespace-sensitive)
4. If 0 matches: fail with "search text not found"
5. If >1 matches: fail with "ambiguous match — provide more context"

### CRLF Normalization
Both the SEARCH text and the file content are normalized CRLF → LF before matching. This prevents Windows line-ending issues.

### Git Commit After Edit
```python
ASSISTANT_AUTHOR = Actor("Arivu Baalan", "arivu@smtw.in")

# After successful pma-edit application:
repo.index.add([str(file_path)])
repo.index.commit(
    f"AI: {commit_summary}",
    author=ASSISTANT_AUTHOR,
    committer=ASSISTANT_AUTHOR,
)
```

## Skills System

### Overview
Skills are progressive-disclosure markdown files. The LLM always sees the short manifest (9 items × ~20 words). Full skill content is loaded on demand when the user invokes a skill.

### Skill File Format (`code/src/prompts/skills/<name>.md`)
```markdown
---
name: daily-review
description: Guide through morning and evening daily review workflow, capture priorities, review yesterday's completion
---

# Daily Review Skill

## When to Use
...

## Morning Review Steps
1. ...

## Evening Review Steps  
1. ...
```

### Skills Manifest in System Prompt
```
## Available Skills
Load a skill with the `load_skill` tool when the user asks to use one.

- **daily-review**: Guide through morning and evening daily review workflow...
- **monthly-planning**: Lead monthly planning session with OKR review...
- **quarterly-planning**: Facilitate quarterly planning and OKR setting...
- **weekly-review**: Weekly retrospective and next-week planning...
- **project-setup**: Initialize a new project with proper structure...
- **email-triage**: Process email inbox systematically...
- **meeting-prep**: Prepare for an upcoming meeting...
- **monthly-compliance**: Monthly compliance and reporting checklist...
- **people-delegation**: Manage delegation and track team commitments...
```

### Loading a Skill
When user says "let's do a weekly review", the LLM:
1. Calls `load_skill("weekly-review")` tool
2. Gets full skill content returned as tool result
3. Uses skill content to guide the conversation

## IndexingService (ChromaDB RAG)

### Embedding Model
- Model: `BAAI/bge-small-en-v1.5` (ONNX format)
- Library: `fastembed` (runs ONNX locally, no GPU needed)
- Embedding singleton: initialized once per process
- Vector dimensions: 384
- Pre-downloaded at Docker build time

### LlamaIndex Pipeline
```python
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.embeddings.fastembed import FastEmbedEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

# Document loading
documents = SimpleDirectoryReader(md_root).load_data()

# Node parsing (splits on markdown headers)
parser = MarkdownNodeParser()
nodes = parser.get_nodes_from_documents(documents)

# Embedding + storage
embed_model = FastEmbedEmbedding("BAAI/bge-small-en-v1.5")
vector_store = ChromaVectorStore(chroma_collection=collection)
index = VectorStoreIndex(nodes, embed_model=embed_model, vector_store=vector_store)
```

### ChromaDB Collection
- Name: `md_corpus`
- Location: `DATA_ROOT/<user>/db/chroma/`
- Persistent client (not ephemeral)
- One collection per user (not shared)

### Metadata Stored Per Chunk
```python
{
    "ou": "ProjectName",      # organizational unit (from folder path)
    "path": "Projects/...",   # relative path from md_root (string)
    "mtime": 1234567890.123,  # file modification time (float)
    "archived": "false",      # "true" | "false" (string, not bool — ChromaDB limitation)
}
```

### Query with Filters
```python
# Standard query (exclude archived)
results = collection.query(
    query_embeddings=embed_model.get_text_embedding(query),
    n_results=top_k,
    where={"$and": [
        {"ou": {"$eq": ou_name}},
        {"archived": {"$eq": "false"}},
    ]}
)

# Archive query (include all)
results = collection.query(
    query_embeddings=...,
    n_results=top_k,
    where={"ou": {"$eq": ou_name}},  # no archived filter
)
```

## Chat History Persistence

Chat history is stored in SQLite (`DATA_ROOT/<user>/db/pma.sqlite3`):

```sql
CREATE TABLE chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,       -- "user" | "assistant"
    content TEXT NOT NULL,    -- message content (JSON for complex content)
    created_at REAL NOT NULL
);
```

- History is read before each chat request
- Full history is appended to messages list
- New messages are persisted BEFORE streaming starts (prevents loss if stream fails)
- `DELETE /api/ai/history` truncates the table for current user

## MCP Server Tools (7 Tools)

The MCP server exposes a subset of tools for external access (e.g. Claude.ai):

1. `read_file` — read corpus file
2. `search_corpus` — semantic search
3. `grep` — pattern search
4. `list_files` — list corpus files
5. `write_file` — write corpus file (immediate, no LLM author)
6. `apply_edit` — apply pma-edit block (LLM-style edit with git commit)
7. `read_src` — read code/src resources
8. `list_src` — list code/src resources

MCP author for git commits: `Arivu Baalan <mcp@smtw.in>` (different email from chat AI edits)

## News Watch (Anthropic Message Batches)

### Overview
News watch uses Anthropic's Message Batches API for async batch processing. This is much cheaper than real-time requests when processing many projects.

### Flow
1. **Nightly submission** (`news_watch_submit_job`, 00:00):
   - For each active project with news watching enabled
   - Creates a batch request: "Find recent news about: {project keywords}"
   - Submits to Anthropic Message Batches API
   - Stores batch ID in task_queue

2. **Polling** (`news_watch_poll_job`, every 300s):
   - Checks status of submitted batches
   - If `status == "ended"`: processes results
   - Writes news summaries to project files under `## News` section
   - Stores results with timestamps

3. **On-demand** (`POST /api/corpus/news-watch`):
   - User can trigger news fetch for specific OU
   - Returns job_id for status polling
   - `GET /api/corpus/news-watch/status/<job_id>` — two-stage status

### Feedback
- `GET /api/corpus/news-feedback` — list feedback
- `POST /api/corpus/news-feedback` — submit 👍/👎 feedback
- Feedback stored in SQLite for future relevance tuning
