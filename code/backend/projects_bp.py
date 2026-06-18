"""
Projects Blueprint — read-only filesystem view of the MD project corpus.

  GET /api/projects                    list all project files grouped by OU
  GET /api/projects/content?path=...   raw content of a single project file
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

# OU-level directories that are NOT project folders
_SKIP_DIRS = {"logs", "archive", "db"}


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def _list_project_files() -> list[str]:
    """Return relative paths (OU/file.md) for all project files, sorted by OU then name."""
    root = config.USER_DATA_ROOT
    result = []
    try:
        for entry in sorted(os.scandir(root), key=lambda e: e.name):
            if (entry.is_dir()
                    and entry.name not in _SKIP_DIRS
                    and not entry.name.startswith(".")):
                ou = entry.name
                for f in sorted(os.scandir(entry.path), key=lambda e: e.name):
                    if f.is_file() and f.name.endswith(".md"):
                        result.append(f"{ou}/{f.name}")
    except FileNotFoundError:
        pass
    return result


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

    ou = rel_path.split("/")[0] if "/" in rel_path else ""

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
    files = _list_project_files()
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
