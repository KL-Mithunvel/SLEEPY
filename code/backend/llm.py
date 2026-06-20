"""
Direct Anthropic SDK AI pipeline.

Public API:
    chat(messages, *, system, tools, model)        -> ChatResult
    chat_stream(messages, *, system, tools, model) -> Iterator[str | dict | ChatResult]

Yields from chat_stream:
    str        — text delta chunk (append to display buffer)
    dict       — tool event: {"type": "tool_start"|"tool_end", "name": ..., "id": ..., ["result": ...]}
    ChatResult — final result object (always the last item yielded)
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Iterator

import anthropic

import config

logger = logging.getLogger(__name__)

DEFAULT_MODEL = config.LLM_DEFAULT_MODEL
MAX_TOOL_ITERATIONS = 8
MAX_TOOL_RESULT_CHARS = 60_000

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], str]

    def to_api_format(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class ChatResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    stop_reason: str = "end_turn"
    tool_calls: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_cache_control(system_blocks: list[dict]) -> list[dict]:
    """Tag the last system block with ephemeral cache control for prompt caching."""
    if not system_blocks:
        return system_blocks
    blocks = list(system_blocks)
    blocks[-1] = dict(blocks[-1])
    blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


_ARCHIVE_PATTERNS = [
    r"\b(last year|previous year|year ago|years ago)\b",
    r"\b(retrospect|recap|review|history|archive)\b",
    r"\b(2023|2024)\b",
]


def _wants_archive(query: str) -> bool:
    """Return True if the query appears to ask about historical/archived content."""
    for pattern in _ARCHIVE_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            return True
    return False


def _normalise_system(system) -> list[dict]:
    if system is None:
        return []
    if isinstance(system, str):
        return [{"type": "text", "text": system}]
    return list(system)


def _run_tool(tool_map: dict, block) -> str:
    tool = tool_map.get(block.name)
    if tool is None:
        return f"[unknown tool: {block.name}]"
    try:
        result = tool.handler(block.input)
    except Exception as exc:
        logger.exception("Tool %s raised: %s", block.name, exc)
        result = f"[tool error: {exc}]"
    if len(result) > MAX_TOOL_RESULT_CHARS:
        result = result[:MAX_TOOL_RESULT_CHARS] + "\n\n[... truncated ...]"
    return result


# ---------------------------------------------------------------------------
# Synchronous chat
# ---------------------------------------------------------------------------

def chat(
    messages: list[dict],
    *,
    system=None,
    tools: list[Tool] | None = None,
    model: str | None = None,
) -> ChatResult:
    """
    Multi-turn synchronous chat with a tool-use loop (up to MAX_TOOL_ITERATIONS).
    Returns a ChatResult with accumulated token counts across all iterations.
    """
    model = model or DEFAULT_MODEL
    tools = tools or []
    tool_map = {t.name: t for t in tools}
    api_tools = [t.to_api_format() for t in tools]
    system_blocks = _normalise_system(system)

    client = _get_client()
    msgs = list(messages)
    all_tool_calls: list[dict] = []

    result_text = ""
    result_model = model
    total_in = total_out = total_cr = total_cw = 0
    stop_reason = "end_turn"

    for _ in range(MAX_TOOL_ITERATIONS):
        kwargs: dict = {
            "model": model,
            "max_tokens": config.LLM_MAX_TOKENS,
            "messages": msgs,
        }
        if system_blocks:
            kwargs["system"] = system_blocks
        if api_tools:
            kwargs["tools"] = api_tools

        response = client.messages.create(**kwargs)
        usage = response.usage
        result_model = response.model
        stop_reason = response.stop_reason or "end_turn"
        total_in += usage.input_tokens
        total_out += usage.output_tokens
        total_cr += getattr(usage, "cache_read_input_tokens", 0) or 0
        total_cw += getattr(usage, "cache_creation_input_tokens", 0) or 0

        text_blocks   = [b for b in response.content if b.type == "text"]
        tool_blocks   = [b for b in response.content if b.type == "tool_use"]

        if text_blocks:
            result_text = "".join(b.text for b in text_blocks)

        if stop_reason != "tool_use" or not tool_blocks:
            break

        tool_results = []
        for block in tool_blocks:
            result = _run_tool(tool_map, block)
            all_tool_calls.append({"name": block.name, "input": block.input, "id": block.id})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        msgs.append({"role": "assistant", "content": response.content})
        msgs.append({"role": "user",      "content": tool_results})

    return ChatResult(
        text=result_text,
        model=result_model,
        input_tokens=total_in,
        output_tokens=total_out,
        cache_read_tokens=total_cr,
        cache_write_tokens=total_cw,
        stop_reason=stop_reason,
        tool_calls=all_tool_calls,
    )


# ---------------------------------------------------------------------------
# Streaming chat
# ---------------------------------------------------------------------------

def chat_stream(
    messages: list[dict],
    *,
    system=None,
    tools: list[Tool] | None = None,
    model: str | None = None,
) -> Iterator:
    """
    Multi-turn streaming chat with a tool-use loop.

    Yields:
        str        — text delta (append to display)
        dict       — {"type": "tool_start", "name": ..., "id": ...}
                     {"type": "tool_end",   "name": ..., "id": ..., "result": ...}
        ChatResult — final summary (always last)
    """
    model = model or DEFAULT_MODEL
    tools = tools or []
    tool_map = {t.name: t for t in tools}
    api_tools = [t.to_api_format() for t in tools]
    system_blocks = _normalise_system(system)

    client = _get_client()
    msgs = list(messages)
    all_tool_calls: list[dict] = []

    full_text = ""
    result_model = model
    total_in = total_out = total_cr = total_cw = 0
    stop_reason = "end_turn"

    for _ in range(MAX_TOOL_ITERATIONS):
        kwargs: dict = {
            "model": model,
            "max_tokens": config.LLM_MAX_TOKENS,
            "messages": msgs,
        }
        if system_blocks:
            kwargs["system"] = system_blocks
        if api_tools:
            kwargs["tools"] = api_tools

        with client.messages.stream(**kwargs) as stream:
            for event in stream:
                etype = event.type

                if etype == "message_start":
                    usage = event.message.usage
                    result_model = event.message.model
                    total_in += usage.input_tokens or 0
                    total_cr += getattr(usage, "cache_read_input_tokens", 0) or 0
                    total_cw += getattr(usage, "cache_creation_input_tokens", 0) or 0

                elif etype == "content_block_start":
                    cb = event.content_block
                    if cb.type == "tool_use":
                        yield {"type": "tool_start", "name": cb.name, "id": cb.id}

                elif etype == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield delta.text
                        full_text += delta.text

                elif etype == "message_delta":
                    delta = event.delta
                    stop_reason = delta.stop_reason or stop_reason
                    if hasattr(event, "usage") and event.usage:
                        total_out += event.usage.output_tokens or 0

            final_msg = stream.get_final_message()
            response_content = final_msg.content
            tool_blocks = [b for b in response_content if b.type == "tool_use"]

        if stop_reason != "tool_use" or not tool_blocks:
            break

        tool_results = []
        for block in tool_blocks:
            result = _run_tool(tool_map, block)
            all_tool_calls.append({"name": block.name, "input": block.input, "id": block.id})
            yield {"type": "tool_end", "name": block.name, "id": block.id, "result": result}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        msgs.append({"role": "assistant", "content": response_content})
        msgs.append({"role": "user",      "content": tool_results})

    yield ChatResult(
        text=full_text,
        model=result_model,
        input_tokens=total_in,
        output_tokens=total_out,
        cache_read_tokens=total_cr,
        cache_write_tokens=total_cw,
        stop_reason=stop_reason,
        tool_calls=all_tool_calls,
    )
