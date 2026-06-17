"""
LiteLLM wrapper for Claude (primary AI provider).

Public API:
    chat(messages, *, model, max_tokens, event_type, conn) → dict
    build_rag_context(query, k)                            → str
    generate_morning_briefing(conn)                        → str
    log_ai_event(conn, ...)                                → int (event_id)
"""

import hashlib
import json
import logging
import sqlite3
import time

import litellm

import config
import md_indexer

logger = logging.getLogger(__name__)

# Route all Claude calls through the Anthropic provider.
litellm.api_key = config.CLAUDE_API_KEY


# ---------------------------------------------------------------------------
# ai_events logging
# ---------------------------------------------------------------------------

def log_ai_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    prompt_hash: str | None = None,
    model: str | None = None,
    diff: str | None = None,
    result: str | None = None,
    accepted: int | None = None,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> int:
    """Insert a row into ai_events and return the new event_id."""
    cur = conn.execute(
        """
        INSERT INTO ai_events
            (event_type, prompt_hash, model, diff, result,
             accepted, latency_ms, input_tokens, output_tokens)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (event_type, prompt_hash, model, diff, result,
         accepted, latency_ms, input_tokens, output_tokens),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Core chat
# ---------------------------------------------------------------------------

def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    event_type: str = "ai_chat",
    conn: sqlite3.Connection | None = None,
) -> dict:
    """
    Call LiteLLM with the given messages.

    Returns:
        {
            "content":       str,
            "model":         str,
            "input_tokens":  int,
            "output_tokens": int,
            "latency_ms":    int,
            "event_id":      int | None,   # set when conn is provided
        }
    """
    model = model or config.LLM_DEFAULT_MODEL
    max_tokens = max_tokens or config.LLM_MAX_TOKENS

    prompt_text = json.dumps(messages, ensure_ascii=False)
    prompt_hash = hashlib.sha256(prompt_text.encode()).hexdigest()[:16]

    t0 = time.monotonic()
    try:
        response = litellm.completion(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            api_key=config.CLAUDE_API_KEY,
        )
    except Exception as exc:
        logger.error("LiteLLM call failed (model=%s): %s", model, exc)
        raise

    latency_ms = int((time.monotonic() - t0) * 1000)
    content = response.choices[0].message.content or ""
    usage = response.usage or {}
    input_tokens = getattr(usage, "prompt_tokens", None)
    output_tokens = getattr(usage, "completion_tokens", None)
    used_model = getattr(response, "model", model)

    event_id = None
    if conn is not None:
        event_id = log_ai_event(
            conn,
            event_type=event_type,
            prompt_hash=prompt_hash,
            model=used_model,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    logger.info(
        "AI call %s | model=%s | in=%s out=%s | %dms",
        event_type, used_model, input_tokens, output_tokens, latency_ms,
    )
    return {
        "content": content,
        "model": used_model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
        "event_id": event_id,
    }


# ---------------------------------------------------------------------------
# RAG context builder
# ---------------------------------------------------------------------------

def build_rag_context(query: str, k: int | None = None) -> str:
    """
    Query ChromaDB for the top-k most relevant MD chunks and format them
    as a <context> block to inject into a prompt.
    """
    k = k or config.LLM_MAX_CONTEXT_CHUNKS
    chunks = md_indexer.query(query, k=k)
    if not chunks:
        return ""

    parts = ["<context>"]
    for c in chunks:
        header = f"[{c['file_path']}]"
        if c["heading"]:
            header += f" § {c['heading']}"
        parts.append(f"{header}\n{c['content']}")
    parts.append("</context>")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Morning briefing generation
# ---------------------------------------------------------------------------

_BRIEFING_SYSTEM = """\
You are a calm, focused personal project assistant. You have access to the user's
project notes. Generate a concise morning briefing that:
- Highlights what's due or overdue today
- Flags any blocked projects
- Suggests a focus plan with no more than 3 priority items
- Uses professional, warm, direct language
- Is formatted in clean Markdown
Do not invent tasks or people. Only reference what is in the provided context.\
"""


def generate_morning_briefing(conn: sqlite3.Connection) -> str:
    """
    Generate today's morning briefing by retrieving active project context
    and calling the LLM. Logs the result to ai_events.result.
    Returns the briefing text.
    """
    from datetime import date
    today = date.today().isoformat()

    query_text = "active tasks due today overdue blocked projects priority"
    context = build_rag_context(query_text, k=config.LLM_MAX_CONTEXT_CHUNKS)

    if not context:
        logger.warning("morning_briefing: no indexed content found — run reindex first")
        result_text = (
            f"# Morning Briefing — {today}\n\n"
            "_No project data indexed yet. Run a reindex and try again._"
        )
        log_ai_event(conn, event_type="morning_briefing", result=result_text)
        return result_text

    messages = [
        {"role": "system", "content": _BRIEFING_SYSTEM},
        {
            "role": "user",
            "content": f"Today is {today}. Please generate my morning briefing.\n\n{context}",
        },
    ]

    resp = chat(messages, event_type="morning_briefing", conn=None)
    result_text = resp["content"]

    log_ai_event(
        conn,
        event_type="morning_briefing",
        model=resp["model"],
        result=result_text,
        latency_ms=resp["latency_ms"],
        input_tokens=resp["input_tokens"],
        output_tokens=resp["output_tokens"],
        accepted=1,
    )
    return result_text
