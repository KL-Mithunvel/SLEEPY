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
import re
import sqlite3
import time
from pathlib import Path

import litellm

import config
import md_indexer
import task_scan

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
You are a calm, focused personal project assistant. You have access to a deterministic \
scan of today's curated tasks (grouped by OU — these are the tasks actually queued for \
today, not every open item across every project) and the raw content of their inbox \
(quick captures, which is where meeting mentions and other free-text notes land; already- \
resolved captures are filtered out before you see them, so everything here is still open). \
Generate a concise morning briefing with these sections, in order:
- ## Today's Schedule — any meetings, calls, or time-bound items you can find in the
  inbox content or task due-dates. If genuinely nothing time-bound is found, say so
  briefly rather than omitting the section.
- ## Due / Overdue — what's due today or overdue, called out by OU
- ## Blocked — anything that looks stalled or blocked, if apparent from the tasks
- ## Focus Plan — no more than 3 priority items for today
Inbox captures carry a `captured DD-MM-YYYY HH:MM` timestamp — use it. For anything that's \
been sitting open for more than a couple of days, say how long it's been pending instead of \
just repeating the capture text verbatim (e.g. "still not scheduled — pending since Jul 4") \
so a genuinely unresolved item reads as an escalating nudge across days, not an identical \
line copy-pasted every morning.
Use professional, warm, direct language. Format in clean Markdown.
Do not invent tasks, people, or meetings. Only reference what is in the provided context.\
"""


def _build_project_task_summary(data_root: str) -> str:
    """Deterministic, grouped-by-OU curated task list — today's Daily files, not
    every open checklist item across every project (see task_scan.scan_todays_tasks)."""
    tasks = task_scan.scan_todays_tasks(data_root)
    if not tasks:
        return "No tasks queued for today (nothing in today's Daily files yet)."

    by_ou: dict[str, list[dict]] = {}
    for t in tasks:
        by_ou.setdefault(t["ou"], []).append(t)

    parts = []
    for ou, ou_tasks in by_ou.items():
        parts.append(f"### {ou} — {len(ou_tasks)} task(s) for today")
        for t in ou_tasks:
            due_str = f" due:{t['due']}" if t["due"] else ""
            parts.append(f"- {t['text']}{due_str}")
    return "\n".join(parts)


_CHECKED_LINE_RE = re.compile(r"^\s*- \[[xX]\]")


def _load_inbox(data_root: str) -> str:
    """
    Raw inbox content, minus already-resolved (- [x]) lines. inbox.md is never
    pruned on its own — items just get checked off in place when resolved — so
    without this filter, a resolved capture keeps getting fed into the briefing
    LLM's context forever and can resurface as if it were still open.
    """
    path = Path(data_root) / "inbox.md"
    if not path.is_file():
        return ""
    raw = path.read_text(encoding="utf-8", errors="replace")
    return "\n".join(line for line in raw.splitlines() if not _CHECKED_LINE_RE.match(line))


def generate_morning_briefing(conn: sqlite3.Connection) -> str:
    """
    Generate today's morning briefing from a deterministic scan of all active
    projects' open tasks plus the raw inbox (for meeting/schedule visibility) —
    not fuzzy semantic search, so it can't silently miss things due to embedding
    relevance. Logs the result to ai_events.result. Returns the briefing text.
    """
    from datetime import date
    today = date.today().isoformat()

    task_summary = _build_project_task_summary(config.USER_DATA_ROOT)
    inbox_content = _load_inbox(config.USER_DATA_ROOT)

    context_parts = [f"<open_tasks_by_project>\n{task_summary}\n</open_tasks_by_project>"]
    if inbox_content:
        context_parts.append(f"<inbox>\n{inbox_content}\n</inbox>")
    context = "\n\n".join(context_parts)

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
