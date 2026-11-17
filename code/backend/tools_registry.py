"""
LLM tool registry — builds the 11 Tool objects per-request.

Call build_tools(conn) at the start of each chat request.
Each handler is a closure that captures the db connection and data roots.
"""

import os
import re
import time

import regex

import config
import md_editor
import md_indexer
import skills
import task_queue
import untrusted
from llm import Tool

_ALLOWED_EMAIL_DOMAIN = "@smtw.in"

# The LLM's regex is untrusted input (prompt injection via news bullets can
# steer it). A length cap alone doesn't stop catastrophic backtracking:
# `(a+)+$` is 7 chars and took 20s on a 29-char line under stdlib `re` (found
# by tooling/sandbox/), and prod runs ONE gunicorn worker, so one injected
# grep froze the whole site. The `regex` engine takes a timeout and also
# short-circuits many nested-quantifier patterns outright; the budget below
# covers the whole search, not each line.
_MAX_GREP_PATTERN_LEN = 200
_GREP_TIME_BUDGET_SEC = 3.0

# One plain address, nothing a mail API might split into several recipients.
_SINGLE_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _is_allowed_email_recipient(to: str) -> bool:
    """
    The chat LLM's context includes externally-controlled text (news bullets
    pulled from the public web via news_watch.py), so send_email is the one
    unconfirmed write action reachable by prompt injection. Restricting
    recipients to the owner or the owner's own company domain closes the
    exfiltration path without requiring a confirm-gate UI for routine mail.
    """
    to = (to or "").strip().lower()
    # fullmatch first: a bare endswith() accepted "attacker@evil.com,me@smtw.in"
    # (also ";" and newline-joined lists) — found by tooling/sandbox/.
    if not _SINGLE_EMAIL_RE.fullmatch(to):
        return False
    if to == (config.USER_EMAIL or "").strip().lower():
        return True
    return to.endswith(_ALLOWED_EMAIL_DOMAIN)


MAX_QUESTION_LEN = 300
MAX_OPTION_LEN = 120
MIN_OPTIONS, MAX_OPTIONS = 2, 4


def clean_question(inp: dict) -> dict:
    """
    Validate an `ask_user` call into {"question", "options"}. Raises ValueError
    with a message the model can act on. The UI appends its own final "type your
    own answer" choice, so the model is told never to add one itself.
    """
    question = " ".join(str(inp.get("question") or "").split())
    if not question:
        raise ValueError("question must not be empty")
    if len(question) > MAX_QUESTION_LEN:
        raise ValueError(f"question is too long (max {MAX_QUESTION_LEN} characters)")
    raw = inp.get("options")
    if not isinstance(raw, list):
        raise ValueError("options must be a list of short strings")
    options: list[str] = []
    for item in raw:
        text = " ".join(str(item).split())
        if not text:
            continue
        if len(text) > MAX_OPTION_LEN:
            raise ValueError(f"each option must be at most {MAX_OPTION_LEN} characters")
        if text.casefold() not in {o.casefold() for o in options}:
            options.append(text)
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise ValueError(f"give between {MIN_OPTIONS} and {MAX_OPTIONS} distinct options")
    return {"question": question, "options": options}


def build_tools(conn, staged_actions: list | None = None,
                questions: list | None = None) -> list[Tool]:
    data_root = config.USER_DATA_ROOT
    src_root = str(config.SRC_ROOT)

    def h_ask_user(inp: dict) -> str:
        try:
            q = clean_question(inp)
        except ValueError as e:
            return f"[error: {e}]"
        if questions is not None:
            if questions:
                return ("[error: you already asked a question this turn. One at a time — "
                        "end your turn and wait for the answer.]")
            questions.append(q)
        return ("Question shown to the user as buttons. End your turn now with no further "
                "text and no more tool calls — their answer arrives as the next message.")

    def _safe_path(base: str, rel_path: str) -> str:
        norm = rel_path.replace("\\", "/").lstrip("/")
        abs_path = os.path.normpath(os.path.join(base, norm))
        base_norm = os.path.normpath(base)
        if not (abs_path.startswith(base_norm + os.sep) or abs_path == base_norm):
            raise ValueError(f"Path outside boundary: {rel_path!r}")
        if not md_editor.is_within_root(base_norm, abs_path):
            raise ValueError(f"Path outside boundary (symlink leaves it): {rel_path!r}")
        return abs_path

    def _safe_corpus_md(rel_path: str) -> str:
        """
        Read-side twin of md_editor.validate_path: inside the data root, not
        under db/ (SQLite with password hashes + chat logs, Chroma index,
        news-watch state), and a .md file. The chat context contains text
        pulled from the public web, so every read tool must assume the
        request may be an injected one.
        """
        abs_path = _safe_path(data_root, rel_path)
        if md_editor.is_reserved_corpus_path(data_root, abs_path):
            raise ValueError(f"db/ and dot-directories are internal and not readable: {rel_path!r}")
        if not abs_path.lower().endswith(".md"):
            raise ValueError(f"Only .md corpus files are readable: {rel_path!r}")
        return abs_path

    def _is_under_db(path: str) -> bool:
        """True for db/ or a dot-directory (.git/) — a directory, so probe a child of it."""
        return md_editor.is_reserved_corpus_path(data_root, os.path.join(path, "_"))

    def h_load_skill(inp: dict) -> str:
        return skills.get_skill_content(inp["name"])

    def h_grep(inp: dict) -> str:
        pattern = inp["pattern"]
        if len(pattern) > _MAX_GREP_PATTERN_LEN:
            return f"[error: pattern longer than {_MAX_GREP_PATTERN_LEN} chars — simplify it]"
        search_dir = data_root
        if inp.get("path"):
            try:
                search_dir = _safe_path(data_root, inp["path"])
            except ValueError as e:
                return f"[error: {e}]"
            if _is_under_db(search_dir):
                return "[error: db/ and dot-directories are internal and not searchable]"
        try:
            compiled = regex.compile(pattern, regex.IGNORECASE)
        except regex.error as e:
            return f"[invalid regex: {e}]"
        deadline = time.monotonic() + _GREP_TIME_BUDGET_SEC
        results = []
        for root, dirs, files in os.walk(search_dir):
            dirs[:] = sorted(d for d in dirs if not _is_under_db(os.path.join(root, d)))
            for fname in sorted(files):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, data_root).replace("\\", "/")
                try:
                    with open(fpath, encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise TimeoutError
                            if compiled.search(line, timeout=remaining):
                                results.append(f"{rel}:{lineno}: {line.rstrip()}")
                except TimeoutError:
                    return (f"[error: search exceeded {_GREP_TIME_BUDGET_SEC:.0f}s — the pattern "
                            "is too expensive; use a simpler, more literal one]")
                except OSError:
                    continue
        if not results:
            return f"No matches for: {pattern}"
        return untrusted.mark_untrusted("\n".join(results[:200]))

    def h_read_file(inp: dict) -> str:
        try:
            abs_path = _safe_corpus_md(inp["path"])
        except ValueError as e:
            return f"[error: {e}]"
        if not os.path.isfile(abs_path):
            return f"[file not found: {inp['path']}]"
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            return untrusted.mark_untrusted(f.read())

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
            if _is_under_db(search_dir):
                return "[error: db/ and dot-directories are internal and not listable]"
        lines = []
        for root, dirs, files in os.walk(search_dir):
            dirs[:] = sorted(d for d in dirs if not _is_under_db(os.path.join(root, d)))
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
        return untrusted.mark_untrusted("\n\n---\n\n".join(parts))

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

    def h_move_file(inp: dict) -> str:
        src_path = inp["src_path"]
        dst_path = inp["dst_path"]
        summary = inp.get("summary") or f"Move {src_path} to {dst_path}"
        try:
            result = md_editor.propose_move(src_path, dst_path, summary, conn)
            if staged_actions is not None:
                staged_actions.append(result)
            eid = result.get("event_id", "?")
            return (
                f"Move staged for user review (event_id={eid}). "
                "Tell the user to look for the card in the UI and click Apply to confirm."
            )
        except ValueError as e:
            return f"[error: {e}]"

    def h_delete_file(inp: dict) -> str:
        file_path = inp["path"]
        summary = inp.get("summary") or f"Delete {file_path}"
        try:
            result = md_editor.propose_delete(file_path, summary, conn)
            if staged_actions is not None:
                staged_actions.append(result)
            eid = result.get("event_id", "?")
            return (
                f"Delete staged for user review (event_id={eid}). "
                "Tell the user to look for the card in the UI and click Apply to confirm."
            )
        except ValueError as e:
            return f"[error: {e}]"

    return [
        Tool(
            name="ask_user",
            description=(
                "Ask the user ONE clarifying question as clickable multiple-choice buttons. Use it "
                "whenever you are not sure what they mean or where something belongs (see 'When "
                "Uncertain'), instead of guessing or asking in prose. Give 2-4 concrete, mutually "
                "exclusive options, most likely first. Do NOT add an 'other' option — the UI always "
                "adds a final 'type my own answer' choice. After calling it, end your turn."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "One short, specific question."},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": MIN_OPTIONS,
                        "maxItems": MAX_OPTIONS,
                        "description": "2-4 distinct answers the user can click.",
                    },
                },
                "required": ["question", "options"],
            },
            handler=h_ask_user,
        ),
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
        Tool(
            name="move_file",
            description=(
                "Relocate or rename an existing markdown file in the user corpus — e.g. fixing a "
                "project written under the wrong OU, or renaming a slug. Use this instead of "
                "write_file when the content isn't changing, only its path. The move is staged "
                "for user confirmation; it is NOT applied immediately."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "src_path": {
                        "type": "string",
                        "description": "Current relative path, e.g. 'SMTW/wrong-ou-project.md'.",
                    },
                    "dst_path": {
                        "type": "string",
                        "description": "New relative path, e.g. 'VIT/wrong-ou-project.md'. Must end in .md and must not already exist.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "One-line reason for the move.",
                    },
                },
                "required": ["src_path", "dst_path"],
            },
            handler=h_move_file,
        ),
        Tool(
            name="delete_file",
            description=(
                "Permanently remove an existing markdown file from the user corpus — e.g. an "
                "orphaned duplicate left behind by a previous move. The delete is staged for "
                "user confirmation, showing the full file content that would be removed; it is "
                "NOT applied immediately."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path of the file to delete, e.g. 'SMTW/Archive/old-duplicate.md'.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "One-line reason for the deletion.",
                    },
                },
                "required": ["path"],
            },
            handler=h_delete_file,
        ),
    ]
