"""
Projects Blueprint — read-only filesystem view of the entire MD corpus
(project files, research notes, People/, Recur/, Daily/, Plans/, Govern/,
root-level files like inbox.md/ABOUT.md — everything except db/).

  GET /api/projects                    list all corpus .md files grouped by top folder
  GET /api/projects/content?path=...   raw content of a single corpus file
"""

import logging
import os
import re

from flask import Blueprint, jsonify, request

import config
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
