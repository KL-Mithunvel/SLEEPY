"""
Projects Blueprint — filesystem view + direct editor for the entire MD corpus
(project files, research notes, People/, Recur/, Daily/, Plans/, Govern/,
root-level files like inbox.md/ABOUT.md — everything except db/).

  GET  /api/projects                    list all corpus .md files grouped by top folder
  GET  /api/projects/content?path=...   raw content of a single corpus file
  PUT  /api/projects/content            save edited content (auto-applied, no confirm)
  GET  /api/projects/structured?path=... structured project data (frontmatter/sections/tasks/lists)
  POST /api/projects/tasks              add/edit/remove a task line
  POST /api/projects/list-item          add/remove a Decisions or Open Questions bullet
  PUT  /api/projects/section            replace a Goal/Why/Current State/Notes section's text
  PUT  /api/projects/status             change status, moving to/from <OU>/Archive/ as needed
"""

import logging
import os
import re

from flask import Blueprint, jsonify, request

import config
import project_editor
from auth_utils import require_perm
from md_indexer import _parse_frontmatter

logger = logging.getLogger(__name__)

projects_bp = Blueprint("projects", __name__)

# Only internal app data is excluded — everything else in the corpus is shown.
_SKIP_DIRNAME = "db"


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def _list_all_md_files() -> list[str]:
    """Return relative posix paths for every .md file under the data root (excludes db/)."""
    root = config.USER_DATA_ROOT
    db_dir = os.path.normpath(os.path.join(root, _SKIP_DIRNAME))
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if not os.path.normpath(os.path.join(dirpath, d)).startswith(db_dir)
            and not d.startswith(".")
        )
        for fname in sorted(filenames):
            if fname.endswith(".md"):
                abs_path = os.path.join(dirpath, fname)
                rel = os.path.relpath(abs_path, root).replace("\\", "/")
                result.append(rel)
    return sorted(result)


def _parse_project(rel_path: str) -> dict | None:
    abs_path = os.path.join(config.USER_DATA_ROOT, rel_path)
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return None

    fm, body = _parse_frontmatter(raw)

    # Title: frontmatter.title → first H1 → filename
    title = str(fm.get("title", "")).strip()
    if not title:
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
    if not title:
        title = (os.path.splitext(os.path.basename(rel_path))[0]
                 .replace("-", " ").replace("_", " ").title())

    status = str(fm.get("status", "")).strip()
    open_tasks = body.count("- [ ]")
    done_tasks = len(re.findall(r"- \[[xX]\]", body))

    # First non-empty non-heading body line as snippet
    snippet = ""
    for line in body.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            snippet = s[:200]
            break

    ou = rel_path.split("/")[0] if "/" in rel_path else "General"

    return {
        "rel_path": rel_path,
        "ou": ou,
        "name": title,
        "status": status,
        "open_tasks": open_tasks,
        "done_tasks": done_tasks,
        "snippet": snippet,
    }


def _safe_project_abs(rel_path: str) -> str | None:
    """Return resolved absolute path only if it's inside USER_DATA_ROOT and ends in .md."""
    norm = os.path.normpath(
        os.path.join(config.USER_DATA_ROOT, rel_path.replace("\\", "/").lstrip("/"))
    )
    root = os.path.normpath(config.USER_DATA_ROOT)
    if norm.startswith(root + os.sep) and norm.endswith(".md"):
        return norm
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@projects_bp.get("/api/projects")
@require_perm("projects:read")
def list_projects():
    files = _list_all_md_files()
    projects = [p for p in (_parse_project(f) for f in files) if p is not None]
    return jsonify({"projects": projects, "total": len(projects)})


@projects_bp.get("/api/projects/content")
@require_perm("projects:read")
def project_content():
    rel = (request.args.get("path") or "").strip()
    if not rel:
        return jsonify({"error": "path is required"}), 400

    abs_path = _safe_project_abs(rel)
    if not abs_path:
        return jsonify({"error": "Invalid path"}), 400

    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except FileNotFoundError:
        return jsonify({"error": "Not found"}), 404

    return jsonify({"rel_path": rel, "content": content})


@projects_bp.put("/api/projects/content")
@require_perm("projects:write")
def save_project_content():
    """
    Direct save from the Projects GUI editor — auto-applied, no confirm step
    (the user already reviewed the content themselves before clicking Save).
    Body: {"path": "...", "content": "..."}
    """
    body = request.get_json(silent=True) or {}
    rel = (body.get("path") or "").strip()
    content = body.get("content", "")
    if not rel:
        return jsonify({"error": "path is required"}), 400

    import md_editor
    from app import get_db

    db = get_db()
    try:
        proposal = md_editor.propose_edit(rel, content, f"Edited via Projects GUI: {rel}", db)
        sha = md_editor.apply_edit(proposal["event_id"], db)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True, "sha": sha[:8]})


# ---------------------------------------------------------------------------
# Structured editor routes
# ---------------------------------------------------------------------------

@projects_bp.get("/api/projects/structured")
@require_perm("projects:read")
def project_structured():
    rel = (request.args.get("path") or "").strip()
    if not rel or not _safe_project_abs(rel):
        return jsonify({"error": "Invalid path"}), 400

    data = project_editor.parse_structured(config.USER_DATA_ROOT, rel)
    if data is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(data)


@projects_bp.post("/api/projects/tasks")
@require_perm("projects:write")
def project_tasks():
    from app import get_db

    body = request.get_json(silent=True) or {}
    rel = (body.get("path") or "").strip()
    action = (body.get("action") or "").strip()
    if not rel or not _safe_project_abs(rel):
        return jsonify({"error": "Invalid path"}), 400

    db = get_db()
    ok = False
    if action == "add":
        text = (body.get("text") or "").strip()
        if not text:
            return jsonify({"error": "text is required"}), 400
        ok = project_editor.add_task(config.USER_DATA_ROOT, rel, text, body.get("priority"), body.get("due"), db)
    elif action == "edit":
        old_line = (body.get("old_line") or "").strip()
        text = (body.get("text") or "").strip()
        if not old_line or not text:
            return jsonify({"error": "old_line and text are required"}), 400
        ok = project_editor.edit_task(
            config.USER_DATA_ROOT, rel, old_line, text,
            bool(body.get("done")), body.get("priority"), body.get("due"), db,
        )
    elif action == "remove":
        old_line = (body.get("old_line") or "").strip()
        if not old_line:
            return jsonify({"error": "old_line is required"}), 400
        ok = project_editor.remove_task(config.USER_DATA_ROOT, rel, old_line, db)
    else:
        return jsonify({"error": f"Unknown action: {action!r}"}), 400

    if not ok:
        return jsonify({"error": "Task operation failed (line not found or write rejected)"}), 400
    return jsonify({"ok": True})


@projects_bp.post("/api/projects/list-item")
@require_perm("projects:write")
def project_list_item():
    from app import get_db

    body = request.get_json(silent=True) or {}
    rel = (body.get("path") or "").strip()
    heading = (body.get("section") or "").strip()
    action = (body.get("action") or "").strip()
    text = (body.get("text") or "").strip()
    if not rel or not _safe_project_abs(rel):
        return jsonify({"error": "Invalid path"}), 400
    if not text:
        return jsonify({"error": "text is required"}), 400

    db = get_db()
    if action == "add":
        ok = project_editor.add_list_item(config.USER_DATA_ROOT, rel, heading, text, db)
    elif action == "remove":
        ok = project_editor.remove_list_item(config.USER_DATA_ROOT, rel, heading, text, db)
    else:
        return jsonify({"error": f"Unknown action: {action!r}"}), 400

    if not ok:
        return jsonify({"error": "List-item operation failed"}), 400
    return jsonify({"ok": True})


@projects_bp.put("/api/projects/section")
@require_perm("projects:write")
def project_section():
    from app import get_db

    body = request.get_json(silent=True) or {}
    rel = (body.get("path") or "").strip()
    heading = (body.get("heading") or "").strip()
    text = body.get("text", "")
    if not rel or not _safe_project_abs(rel):
        return jsonify({"error": "Invalid path"}), 400
    if not heading:
        return jsonify({"error": "heading is required"}), 400

    db = get_db()
    ok = project_editor.update_section_text(config.USER_DATA_ROOT, rel, heading, text, db)
    if not ok:
        return jsonify({"error": "Section update failed"}), 400
    return jsonify({"ok": True})


@projects_bp.put("/api/projects/status")
@require_perm("projects:write")
def project_status():
    from app import get_db

    body = request.get_json(silent=True) or {}
    rel = (body.get("path") or "").strip()
    status = (body.get("status") or "").strip()
    if not rel or not _safe_project_abs(rel):
        return jsonify({"error": "Invalid path"}), 400

    db = get_db()
    new_rel_path = project_editor.set_status(config.USER_DATA_ROOT, rel, status, db)
    if new_rel_path is None:
        return jsonify({"error": "Status change failed"}), 400
    return jsonify({"ok": True, "rel_path": new_rel_path})
