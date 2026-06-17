"""
AI Blueprint — routes for indexing, querying, and AI-assisted MD editing.

Endpoints:
  POST /api/ai/reindex              enqueue a full md_reindex task
  GET  /api/ai/query?q=&k=          semantic search over the indexed corpus
  POST /api/ai/suggest              RAG + LLM: generate a response for a given query
  POST /api/ai/edit                 propose an AI MD edit (returns diff, requires confirm)
  POST /api/ai/edit/<id>/confirm    apply a pending edit
  POST /api/ai/edit/<id>/reject     cancel a pending edit
"""

import logging

from flask import Blueprint, g, jsonify, request

import ai_client
import md_editor
import md_indexer
import task_queue
from auth_utils import require_perm

logger = logging.getLogger(__name__)

ai_bp = Blueprint("ai", __name__)


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
    k = min(int(request.args.get("k", 5)), 20)

    chunks = md_indexer.query(q, k=k)
    return jsonify({"items": chunks, "total": len(chunks)})


# ---------------------------------------------------------------------------
# AI suggest (RAG + LLM)
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
    """
    Ask the AI to edit a specific MD file based on an instruction.
    The AI returns a proposed new file content; we compute the diff and
    return it for user confirmation before writing anything to disk.

    Request body:
        {
          "file_path": "SMTW/project.md",    relative to USER_DATA_ROOT
          "instruction": "Add task: review budget due 2026-06-30"
        }

    Response:
        {
          "event_id": 42,
          "diff": "...",
          "rel_path": "SMTW/project.md",
          "summary": "...",
          "is_new": false
        }
    """
    body = request.get_json(silent=True) or {}
    rel_path = (body.get("file_path") or "").strip()
    instruction = (body.get("instruction") or "").strip()

    if not rel_path:
        return jsonify({"error": "file_path is required"}), 400
    if not instruction:
        return jsonify({"error": "instruction is required"}), 400

    import os
    abs_path = os.path.join(__import__("config").USER_DATA_ROOT, rel_path.replace("\\", "/").lstrip("/"))
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
    except Exception as exc:
        logger.exception("LLM call failed during edit proposal")
        return jsonify({"error": str(exc)}), 502

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
# Helpers
# ---------------------------------------------------------------------------

def _db():
    """Return the per-request DB connection from Flask g."""
    from app import get_db
    return get_db()
