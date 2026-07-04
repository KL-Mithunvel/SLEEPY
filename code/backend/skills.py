"""
Skills system — YAML-frontmatter skill files in code/src/prompts/skills/.

Public API:
    get_skill_content(name) -> str   full body of the named skill (frontmatter stripped)
    get_manifest()          -> str   bullet-list of all skills for the system prompt
"""

import re
from pathlib import Path

import config

_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (meta dict, body text) split from YAML frontmatter."""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def get_skill_content(name: str) -> str:
    """Return the body of a skill file (frontmatter stripped). Returns error string if not found or invalid."""
    # `name` is LLM-controlled (the load_skill tool) — resolve and check containment
    # before touching the filesystem, same boundary check as tools_registry._safe_path.
    if not name:
        return f"[invalid skill name {name!r} — available: {_list_names()}]"

    skills_dir = config.SKILLS_DIR.resolve()
    path = (skills_dir / f"{name}.md").resolve()
    if path.parent != skills_dir:
        return f"[invalid skill name {name!r} — available: {_list_names()}]"
    if not path.exists():
        return f"[skill '{name}' not found — available: {_list_names()}]"
    _, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    return body.strip()


def get_manifest() -> str:
    """Return a formatted manifest of all available skills for inclusion in the system prompt."""
    if not config.SKILLS_DIR.exists():
        return ""
    lines: list[str] = []
    for skill_path in sorted(config.SKILLS_DIR.glob("*.md")):
        text = skill_path.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(text)
        name = meta.get("name", skill_path.stem)
        desc = meta.get("description", "")
        lines.append(f"- **{name}**: {desc}")
    if not lines:
        return ""
    header = (
        "## Available Skills\n"
        "Load a skill with the `load_skill` tool when the user asks to use one.\n\n"
    )
    return header + "\n".join(lines)


def _list_names() -> str:
    if not config.SKILLS_DIR.exists():
        return "(none)"
    return ", ".join(p.stem for p in sorted(config.SKILLS_DIR.glob("*.md")))
