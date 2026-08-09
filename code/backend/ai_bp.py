"""
AI Blueprint — routes for indexing, querying, and AI-assisted MD editing.

Endpoints:
  POST /api/ai/reindex              enqueue a full md_reindex task
  GET  /api/ai/query?q=&k=          semantic search over the indexed corpus
  POST /api/ai/suggest              RAG + LLM: generate a response for a given query
  POST /api/ai/edit                 propose an AI MD edit (returns diff, requires confirm)
  POST /api/ai/edit/<id>/confirm    apply a pending edit
  POST /api/ai/edit/<id>/reject     cancel a pending edit
  POST /api/ai/chat                 streaming chat (text/event-stream SSE)
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import anthropic
from flask import Blueprint, Response, g, jsonify, request, stream_with_context

import ai_client
import llm
import md_editor
import md_indexer
import skills
import task_queue
import tools_registry
from auth_utils import require_perm

logger = logging.getLogger(__name__)

ai_bp = Blueprint("ai", __name__)

_IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# System prompt — hot-reloaded from disk each request
# ---------------------------------------------------------------------------

def _load_system_prompt() -> str:
    import config
    path = config.SYSTEM_PROMPT_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        logger.warning("SystemPrompt.MD not found at %s — using fallback", path)
        return "You are a calm personal project-management assistant."


def _list_corpus_files() -> str:
    """Return a flat list of all .md files in the corpus (excluding db/)."""
    data_root = config.USER_DATA_ROOT
    db_dir = os.path.normpath(os.path.join(data_root, "db"))
    lines = []
    for root, dirs, files in os.walk(data_root):
        dirs[:] = sorted(
            d for d in dirs
            if not os.path.normpath(os.path.join(root, d)).startswith(db_dir)
        )
        rel_root = os.path.relpath(root, data_root).replace("\\", "/")
        for fname in sorted(files):
            if fname.endswith(".md"):
                path = f"{rel_root}/{fname}" if rel_root != "." else fname
                lines.append(path)
    return "\n".join(lines) if lines else "(no files yet — corpus is empty)"


def _build_system(last_user_msg: str, ou: str) -> list[dict]:
    """Build the 4-block system prompt with cache control on the last block."""
    now = datetime.now(_IST)
    date_str = now.strftime("%d-%m-%Y")
    time_str = now.strftime("%H:%M IST")
    weekday = now.strftime("%A")

    try:
        file_list = _list_corpus_files()
    except Exception:
        file_list = "(could not list files)"

    ctx_lines = [
        "## Current Context",
        "",
        f"Date: {date_str} ({weekday})",
        f"Time: {time_str}",
        "",
        "## Corpus Files",
        "",
        file_list,
    ]
    if ou:
        ctx_lines.append(f"\nActive OU: {ou}")
    ctx_block = "\n".join(ctx_lines)

    # RAG context from semantic search
    try:
        rag_text = ai_client.build_rag_context(last_user_msg)
    except Exception:
        rag_text = ""

    if rag_text:
        rag_block = f"## Corpus Context (semantic search results)\n\n{rag_text}"
    else:
        rag_block = (
            "## Corpus Context\n\n"
            "(No indexed content available — run a reindex if the corpus is empty, "
            "or go ahead and create new files if the user is asking you to create something.)"
        )

    # Skills manifest
    try:
        manifest = skills.get_manifest()
        skills_block = f"## Available Skills\n\n{manifest}"
    except Exception:
        skills_block = "## Available Skills\n\n(No skills loaded)"

    blocks = [
        {"type": "text", "text": _load_system_prompt()},
        {"type": "text", "text": ctx_block},
        {"type": "text", "text": rag_block},
        {"type": "text", "text": skills_block},
    ]
    return llm._apply_cache_control(blocks)


# ---------------------------------------------------------------------------
# pma-edit block parsing
# ---------------------------------------------------------------------------

_PMA_EDIT_RE = re.compile(
    r"```pma-edit\s*\n"
    r"file:\s*([^\n]+)\n"
    r"<{7}\s*SEARCH\s*\n"
    r"(.*?)"
    r"={7}[^\n]*\n"
    r"(.*?)"
    r">{7}\s*REPLACE\s*\n?"
    r"```",
    re.DOTALL,
)


def _stage_pma_edits(text: str, conn) -> list[dict]:
    """
    Parse pma-edit blocks from AI response text and stage each one as a
    pending ai_events row. Returns a list of propose_edit result dicts.
    """
    import config
    actions = []
    for m in _PMA_EDIT_RE.finditer(text):
        file_path = m.group(1).strip()
        search_content = m.group(2)
        replace_content = m.group(3)

        try:
            md_editor.validate_path(file_path)
        except ValueError as e:
            logger.warning("pma-edit skipped — validation error: %s", e)
            continue

        norm = file_path.replace("\\", "/").lstrip("/")
        abs_path = os.path.join(config.USER_DATA_ROOT, norm)

        if os.path.isfile(abs_path):
            with open(abs_path, encoding="utf-8", errors="replace") as f:
                current = f.read()
        else:
            current = ""

        # Apply SEARCH/REPLACE
        if search_content.strip() == "":
            # New file: replace content IS the new content
            new_content = replace_content
        else:
            if search_content not in current:
                logger.warning("pma-edit SEARCH not found in %s — skipping", file_path)
                continue
            if current.count(search_content) > 1:
                logger.warning("pma-edit SEARCH matches >1 times in %s — skipping", file_path)
                continue
            new_content = current.replace(search_content, replace_content, 1)

        if not new_content or not new_content.strip():
            continue

        summary = f"Edit {norm}"
        try:
            result = md_editor.propose_edit(file_path, new_content, summary, conn)
            actions.append(result)
        except ValueError as e:
            logger.warning("pma-edit propose_edit failed for %s: %s", file_path, e)

    return actions


# ---------------------------------------------------------------------------
# Reindex
# ---------------------------------------------------------------------------

@ai_bp.post("/api/ai/reindex")
@require_perm("admin:reindex")
def reindex():
    db = _db()
    task_id = task_queue.enqueue(db, "md_reindex", {"full": True})
    return jsonify({"task_id": task_id, "message": "Reindex queued"}), 202


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------

@ai_bp.get("/api/ai/query")
@require_perm("ai:suggest")
def query():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "q is required"}), 400
    try:
        k = min(int(request.args.get("k", 5)), 20)
    except ValueError:
        return jsonify({"error": "k must be an integer"}), 400

    chunks = md_indexer.query(q, k=k)
    return jsonify({"items": chunks, "total": len(chunks)})


# ---------------------------------------------------------------------------
# AI suggest (RAG + LLM — legacy JSON endpoint, kept for internal use)
# ---------------------------------------------------------------------------

@ai_bp.post("/api/ai/suggest")
@require_perm("ai:suggest")
def suggest():
    body = request.get_json(silent=True) or {}
    query_text = (body.get("query") or "").strip()
    if not query_text:
        return jsonify({"error": "query is required"}), 400

    context = ai_client.build_rag_context(query_text, k=body.get("k", None))

    system_msg = (
        "You are a calm personal project assistant. "
        "Answer concisely using the project context below. "
        "Do not invent projects, tasks, or people not present in the context."
    )
    user_msg = query_text
    if context:
        user_msg = f"{query_text}\n\n{context}"

    db = _db()
    resp = ai_client.chat(
        [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        event_type="ai_suggest",
        conn=db,
    )

    return jsonify({
        "response": resp["content"],
        "event_id": resp["event_id"],
        "model": resp["model"],
        "usage": {
            "input_tokens": resp["input_tokens"],
            "output_tokens": resp["output_tokens"],
        },
    })


# ---------------------------------------------------------------------------
# AI MD edit — propose
# ---------------------------------------------------------------------------

@ai_bp.post("/api/ai/edit")
@require_perm("ai:edit_md")
def edit_propose():
    body = request.get_json(silent=True) or {}
    rel_path = (body.get("file_path") or "").strip()
    instruction = (body.get("instruction") or "").strip()

    if not rel_path:
        return jsonify({"error": "file_path is required"}), 400
    if not instruction:
        return jsonify({"error": "instruction is required"}), 400

    # Validate path before reading — prevents path traversal leaking file contents to the LLM
    try:
        norm = md_editor.validate_path(rel_path)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    import config as _cfg
    abs_path = os.path.normpath(os.path.join(_cfg.USER_DATA_ROOT, norm))
    if os.path.isfile(abs_path):
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            current_content = f.read()
    else:
        current_content = ""

    context = ai_client.build_rag_context(instruction)

    system_msg = (
        "You are a precise Markdown editor. "
        "The user will give you a Markdown file and an instruction. "
        "Return ONLY the complete new content of the file — no explanation, "
        "no markdown code fences around it. Preserve all existing structure "
        "and formatting unless the instruction requires changes. "
        "Do not invent tasks or people not already present."
    )
    user_content = (
        f"File: {rel_path}\n\n"
        f"Current content:\n{current_content or '(empty — new file)'}\n\n"
        f"Instruction: {instruction}"
    )
    if context:
        user_content += f"\n\nAdditional context from the corpus:\n{context}"

    db = _db()
    try:
        resp = ai_client.chat(
            [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_content},
            ],
            event_type="ai_edit_propose",
            conn=db,
        )
    except Exception:
        logger.exception("LLM call failed during edit proposal")
        return jsonify({"error": "AI service unavailable — check server logs"}), 502

    new_content = resp["content"].strip()
    summary = instruction[:120]

    try:
        result = md_editor.propose_edit(rel_path, new_content, summary, db)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(result), 200


# ---------------------------------------------------------------------------
# AI MD edit — confirm / reject
# ---------------------------------------------------------------------------

@ai_bp.post("/api/ai/edit/<int:event_id>/confirm")
@require_perm("ai:edit_md")
def edit_confirm(event_id: int):
    db = _db()
    try:
        sha = md_editor.apply_edit(event_id, db)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({"committed": True, "sha": sha})


@ai_bp.post("/api/ai/edit/<int:event_id>/reject")
@require_perm("ai:edit_md")
def edit_reject(event_id: int):
    db = _db()
    md_editor.reject_edit(event_id, db)
    return jsonify({"rejected": True})


# ---------------------------------------------------------------------------
# AI Chat — SSE streaming with tool use
# ---------------------------------------------------------------------------

@ai_bp.post("/api/ai/chat")
@require_perm("ai:suggest")
def chat_endpoint():
    """
    SSE streaming chat with tool use and pma-edit block parsing.

    Request:
        {
          "messages": [{"role": "user"|"assistant", "content": "..."}],
          "ou": "SMTW"   (optional — active organisational unit)
        }

    SSE event types:
        {"type": "delta",        "text": "..."}
        {"type": "tool_progress","event": {"type": "tool_start"|"tool_end", "name": ..., "id": ...}}
        {"type": "done",         "result": {text, model, tokens, actions: [...]}}
        {"type": "error",        "message": "..."}
    """
    body = request.get_json(silent=True) or {}
    messages = body.get("messages") or []
    ou = (body.get("ou") or "").strip()

    if not messages or not any(m.get("role") == "user" for m in messages):
        return jsonify({"error": "messages must contain at least one user message"}), 400

    # Extract last user message for RAG context
    last_user_msg = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )

    def generate():
        # Open an independent connection — g.db gets torn down during SSE streaming
        import local_db as _local_db
        db = _local_db.get_db()
        try:
            system_blocks = _build_system(last_user_msg, ou)
            staged_by_tools: list = []
            llm_tools = tools_registry.build_tools(conn=db, staged_actions=staged_by_tools)
            result = None

            for chunk in llm.chat_stream(
                messages,
                system=system_blocks,
                tools=llm_tools,
            ):
                if isinstance(chunk, str):
                    yield f"data: {json.dumps({'type': 'delta', 'text': chunk})}\n\n"
                elif isinstance(chunk, dict):
                    yield f"data: {json.dumps({'type': 'tool_progress', 'event': chunk})}\n\n"
                elif isinstance(chunk, llm.ChatResult):
                    result = chunk

            if result:
                # Collect actions: tool-staged + any pma-edit blocks in response text
                actions = staged_by_tools + _stage_pma_edits(result.text, db)

                # Log to ai_events — persists the full prompt/response text so
                # past conversations are recoverable, not just token counts.
                try:
                    db.execute(
                        """
                        INSERT INTO ai_events
                            (event_type, model, input_tokens, output_tokens, accepted,
                             user_message, response_text)
                        VALUES ('ai_chat', ?, ?, ?, 1, ?, ?)
                        """,
                        (result.model, result.input_tokens, result.output_tokens,
                         last_user_msg, result.text),
                    )
                    db.commit()
                except Exception:
                    logger.exception("Failed to log ai_event for chat")

                done_payload = {
                    "text": result.text,
                    "model": result.model,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cache_read_tokens": result.cache_read_tokens,
                    "cache_write_tokens": result.cache_write_tokens,
                    "stop_reason": result.stop_reason,
                    "tool_calls": [
                        {"name": tc["name"], "id": tc.get("id", "")}
                        for tc in result.tool_calls
                    ],
                    "actions": actions,
                }
                yield f"data: {json.dumps({'type': 'done', 'result': done_payload})}\n\n"

        except anthropic.AuthenticationError:
            llm.reset_client()
            logger.warning("Anthropic auth error — Claude Code token may need refresh")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Auth token expired. Run any claude command in your terminal to refresh, then retry.'})}\n\n"
        except anthropic.RateLimitError:
            logger.warning("Anthropic rate limit hit (429)")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Rate limit hit — wait a few seconds and try again. (Claude.ai Pro has limited API call bursts.)'})}\n\n"
        except Exception:
            logger.exception("Chat stream error")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Internal error — check server logs'})}\n\n"
        finally:
            _local_db.return_db(db)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db():
    from app import get_db
    return get_db()
