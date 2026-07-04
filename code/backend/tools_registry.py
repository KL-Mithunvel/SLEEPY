"""
LLM tool registry — builds the 9 Tool objects per-request.

Call build_tools(conn) at the start of each chat request.
Each handler is a closure that captures the db connection and data roots.
"""

import os
import re

import config
import md_editor
import md_indexer
import skills
import task_queue
from llm import Tool

_ALLOWED_EMAIL_DOMAIN = "@smtw.in"


def _is_allowed_email_recipient(to: str) -> bool:
    """
    The chat LLM's context includes externally-controlled text (news bullets
    pulled from the public web via news_watch.py), so send_email is the one
    unconfirmed write action reachable by prompt injection. Restricting
    recipients to the owner or the owner's own company domain closes the
    exfiltration path without requiring a confirm-gate UI for routine mail.
    """
    to = (to or "").strip().lower()
    if not to:
        return False
    if to == (config.USER_EMAIL or "").strip().lower():
        return True
    return to.endswith(_ALLOWED_EMAIL_DOMAIN)


def build_tools(conn, staged_actions: list | None = None) -> list[Tool]:
    data_root = config.USER_DATA_ROOT
    src_root = str(config.SRC_ROOT)

    def _safe_path(base: str, rel_path: str) -> str:
        norm = rel_path.replace("\\", "/").lstrip("/")
        abs_path = os.path.normpath(os.path.join(base, norm))
        base_norm = os.path.normpath(base)
        if not (abs_path.startswith(base_norm + os.sep) or abs_path == base_norm):
            raise ValueError(f"Path outside boundary: {rel_path!r}")
        return abs_path

    def h_load_skill(inp: dict) -> str:
        return skills.get_skill_content(inp["name"])

    def h_grep(inp: dict) -> str:
        pattern = inp["pattern"]
        search_dir = data_root
        if inp.get("path"):
            try:
                search_dir = _safe_path(data_root, inp["path"])
            except ValueError as e:
                return f"[error: {e}]"
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return f"[invalid regex: {e}]"
        results = []
        for root, dirs, files in os.walk(search_dir):
            dirs.sort()
            for fname in sorted(files):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, data_root).replace("\\", "/")
                try:
                    with open(fpath, encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append(f"{rel}:{lineno}: {line.rstrip()}")
                except OSError:
                    continue
        if not results:
            return f"No matches for: {pattern}"
        return "\n".join(results[:200])

    def h_read_file(inp: dict) -> str:
        try:
            abs_path = _safe_path(data_root, inp["path"])
        except ValueError as e:
            return f"[error: {e}]"
        if not os.path.isfile(abs_path):
            return f"[file not found: {inp['path']}]"
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            return f.read()

    def h_read_src(inp: dict) -> str:
        try:
            abs_path = _safe_path(src_root, inp["path"])
        except ValueError as e:
            return f"[error: {e}]"
        if not os.path.isfile(abs_path):
            return f"[src file not found: {inp['path']}]"
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            return f.read()

    def h_list_src(inp: dict) -> str:
        search_dir = src_root
        if inp.get("path"):
            try:
                search_dir = _safe_path(src_root, inp["path"])
            except ValueError as e:
                return f"[error: {e}]"
        lines = []
        for root, dirs, files in os.walk(search_dir):
            dirs.sort()
            rel_root = os.path.relpath(root, src_root).replace("\\", "/")
            for fname in sorted(files):
                path = f"{rel_root}/{fname}" if rel_root != "." else fname
                lines.append(path)
        return "\n".join(lines) if lines else "(empty)"

    def h_list_files(inp: dict) -> str:
        search_dir = data_root
        if inp.get("path"):
            try:
                search_dir = _safe_path(data_root, inp["path"])
            except ValueError as e:
                return f"[error: {e}]"
        db_dir = os.path.normpath(os.path.join(data_root, "db"))
        lines = []
        for root, dirs, files in os.walk(search_dir):
            dirs[:] = sorted(
                d for d in dirs
                if not os.path.normpath(os.path.join(root, d)).startswith(db_dir)
            )
            rel_root = os.path.relpath(root, data_root).replace("\\", "/")
            for fname in sorted(files):
                if fname.endswith(".md"):
                    path = f"{rel_root}/{fname}" if rel_root != "." else fname
                    lines.append(path)
        return "\n".join(lines) if lines else "(no .md files)"

    def h_search_corpus(inp: dict) -> str:
        query = inp["query"]
        k = int(inp.get("k") or 5)
        chunks = md_indexer.query(query, k=k)
        if not chunks:
            return "No relevant chunks found."
        parts = []
        for c in chunks:
            header = f"[{c['file_path']}]"
            if c.get("heading"):
                header += f" § {c['heading']}"
            header += f" (score: {c.get('score', '?')})"
            parts.append(f"{header}\n{c['content']}")
        return "\n\n---\n\n".join(parts)

    def h_send_email(inp: dict) -> str:
        to = inp["to"]
        if not _is_allowed_email_recipient(to):
            return (
                f"[error: {to!r} is not an allowed recipient. This assistant can only email "
                "the account owner or addresses on the smtw.in domain — ask the user to send "
                "to anyone else themselves.]"
            )
        tid = task_queue.enqueue(conn, "email", {
            "to": to, "subject": inp["subject"], "body": inp["body"],
        })
        return f"Email queued (task_id={tid})"

    def h_write_file(inp: dict) -> str:
        file_path = inp["path"]
        content = inp["content"]
        summary = inp.get("summary", "")
        try:
            result = md_editor.propose_edit(file_path, content, summary, conn)
            if staged_actions is not None:
                staged_actions.append(result)
            eid = result.get("event_id", "?")
            return (
                f"File staged for user review (event_id={eid}). "
                "Tell the user to look for the diff card in the UI and click Apply to save."
            )
        except ValueError as e:
            return f"[error: {e}]"

    return [
        Tool(
            name="load_skill",
            description="Load the full content of a named skill workflow (e.g. 'daily-review', 'weekly-review').",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill file stem, e.g. 'daily-review'"},
                },
                "required": ["name"],
            },
            handler=h_load_skill,
        ),
        Tool(
            name="grep",
            description="Search for a regex pattern across corpus markdown files. Returns matching lines with file:line context.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "Subdirectory to limit search (optional)"},
                },
                "required": ["pattern"],
            },
            handler=h_grep,
        ),
        Tool(
            name="read_file",
            description="Read a markdown file from the user corpus.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from md_root, e.g. 'SMTW/project.md'"},
                },
                "required": ["path"],
            },
            handler=h_read_file,
        ),
        Tool(
            name="read_src",
            description="Read a file from code/src/ (prompts, templates, help docs, skill files).",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from code/src/"},
                },
                "required": ["path"],
            },
            handler=h_read_src,
        ),
        Tool(
            name="list_src",
            description="List files in code/src/ (skills, templates, help docs).",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Subdirectory within code/src/ (optional)"},
                },
            },
            handler=h_list_src,
        ),
        Tool(
            name="list_files",
            description="List markdown files in the user corpus.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Subdirectory to list (optional)"},
                },
            },
            handler=h_list_files,
        ),
        Tool(
            name="search_corpus",
            description="Semantic search over the corpus using vector embeddings. Use when grep is too specific or you need conceptual matches.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query"},
                    "k": {"type": "integer", "description": "Number of results (default 5)"},
                    "include_archive": {"type": "boolean", "description": "Include archived files (default false)"},
                },
                "required": ["query"],
            },
            handler=h_search_corpus,
        ),
        Tool(
            name="send_email",
            description="Queue an email to be sent. Only use when the user explicitly asks to send an email.",
            input_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string"},
                    "body": {"type": "string", "description": "Email body (plain text)"},
                },
                "required": ["to", "subject", "body"],
            },
            handler=h_send_email,
        ),
        Tool(
            name="write_file",
            description=(
                "Create a new markdown file OR completely overwrite an existing one in the user corpus. "
                "Use this whenever the user asks you to create, write, or save a project file, log entry, "
                "or any other corpus document. The file is staged for user confirmation — it is NOT written "
                "immediately. Always use this tool rather than describing what you would write."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from data root, e.g. 'SMTW/finance-review.md' or 'inbox.md'. Must end in .md.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full markdown content for the file.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "One-line description of what this file is / why it was created.",
                    },
                },
                "required": ["path", "content"],
            },
            handler=h_write_file,
        ),
    ]
