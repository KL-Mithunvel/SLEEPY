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
# AI Chat — natural language interface that routes to answer or edit
# ---------------------------------------------------------------------------

_CHAT_SYSTEM = """\
You are a calm, precise personal project-management assistant for KL Mithunvel (klm@smtw.in).

## Data layout
All project data lives as Markdown files under the user's data root. The structure is:

  <OU>/<project-slug>.md        — one file per project (e.g. SMTW/finance-review.md)
  logs/YYYY-MM-DD.md            — daily log files
  logs/YYYY-WNN.md              — weekly review files
  inbox.md                      — quick captures and unprocessed items
  ABOUT.md                      — user profile
  People.md                     — contacts

OU (Organisational Unit) is a top-level folder grouping related projects.
You may encounter OUs such as SMTW, Infra, Personal, or others the user mentions.
When creating a NEW project, pick a sensible OU and slug from the user's instruction.

## Standard project file format
A new project file should follow this structure:

  # <Project Title>

  **Status:** Active | On Hold | Complete
  **OU:** <OU name>
  **Started:** <DD-MM-YYYY>

  ## Overview
  <one paragraph describing the project>

  ## Tasks
  - [ ] <task description>  <!-- due: DD-MM-YYYY -->
  - [x] <completed task>

  ## Notes
  <any other context>

## Classify every message as QUESTION or INSTRUCTION

QUESTION — user wants information (status, summary, what's due, who is working on what, etc.)
Reply with:
INTENT: answer
RESPONSE:
[concise answer using only what is in the context; if the corpus is empty say so clearly]

INSTRUCTION — user wants to create, update, or capture something
Reply with:
INTENT: edit
FILE: <relative path, e.g. SMTW/finance-review.md or inbox.md>
SUMMARY: <one-line description of the change>
CONTENT:
<the COMPLETE new content of the file — every single line>

## Rules
- For INTENT: edit, always output the COMPLETE file content after CONTENT: — never a partial snippet
- You MAY create new files that do not yet exist — set FILE to the correct new path
- If you cannot determine the right OU or file, use INTENT: answer to ask one clarifying question
- Never fabricate tasks, people, or decisions not stated by the user or present in the context
- Dates are formatted DD-MM-YYYY\
"""


def _parse_chat_response(raw: str) -> dict:
    lines = raw.strip().split("\n")
    intent = "answer"
    file_path = summary = ""
    content_lines: list[str] = []
    response_lines: list[str] = []
    content_started = response_started = False

    for line in lines:
        s = line.strip()
        if s == "INTENT: edit":
            intent = "edit"
        elif s == "INTENT: answer":
            intent = "answer"
        elif s.startswith("FILE:") and intent == "edit":
            file_path = s[5:].strip()
        elif s.startswith("SUMMARY:") and intent == "edit":
            summary = s[8:].strip()
        elif s == "CONTENT:" and intent == "edit":
            content_started = True
        elif s == "RESPONSE:" and intent == "answer":
            response_started = True
        elif content_started and intent == "edit":
            content_lines.append(line)
        elif response_started and intent == "answer":
            response_lines.append(line)

    if intent == "edit":
        return {"intent": "edit", "file_path": file_path,
                "summary": summary, "content": "\n".join(content_lines)}

    answer = "\n".join(response_lines).strip() if response_lines else raw.strip()
    return {"intent": "answer", "content": answer}


@ai_bp.post("/api/ai/chat")
@require_perm("ai:suggest")
def chat_endpoint():
    """
    Natural-language chat endpoint.

    Request:
        { "message": "...", "history": [{"role": "user"|"assistant", "content": "..."}] }

    Response (answer):
        { "type": "answer", "content": "..." }

    Response (edit proposal):
        { "type": "edit", "event_id": 42, "rel_path": "...", "diff": "...", "summary": "..." }
    """
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    history = body.get("history") or []

    if not message:
        return jsonify({"error": "message is required"}), 400

    context = ai_client.build_rag_context(message)

    messages = [{"role": "system", "content": _CHAT_SYSTEM}]
    for h in history[-8:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})

    if context:
        user_content = f"{message}\n\n{context}"
    else:
        # No indexed content — AI can still CREATE new files; it just cannot query existing ones.
        # Do not block the request: let the AI decide based on intent.
        user_content = (
            f"{message}\n\n"
            "[System note: The project corpus index is currently empty — no existing Markdown "
            "files have been indexed yet. If the user is asking about existing projects or tasks, "
            "tell them nothing is indexed and they should run a reindex. If the user is asking you "
            "to CREATE something new, go ahead and create it using the file layout described in "
            "your system prompt.]"
        )
    messages.append({"role": "user", "content": user_content})

    db = _db()
    try:
        resp = ai_client.chat(messages, event_type="ai_chat", conn=db)
    except Exception as exc:
        logger.exception("Chat LLM call failed")
        return jsonify({"error": str(exc)}), 502

    parsed = _parse_chat_response(resp["content"])

    if parsed["intent"] == "edit":
        if not parsed["file_path"] or not parsed["content"]:
            return jsonify({"type": "answer",
                            "content": "I couldn't determine which file to edit. Could you be more specific?"})
        try:
            result = md_editor.propose_edit(parsed["file_path"], parsed["content"], parsed["summary"], db)
        except ValueError as exc:
            return jsonify({"type": "answer", "content": f"I couldn't apply that edit: {exc}"})

        return jsonify({
            "type": "edit",
            "event_id": result["event_id"],
            "rel_path": result["rel_path"],
            "diff": result["diff"],
            "summary": result["summary"],
            "is_new": result.get("is_new", False),
        })

    return jsonify({"type": "answer", "content": parsed["content"]})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db():
    """Return the per-request DB connection from Flask g."""
    from app import get_db
    return get_db()
