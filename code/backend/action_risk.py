"""
Risk warnings for AI-proposed corpus changes, shown on the Apply card.

The card's summary line is written by the model, so a model steered by
injected web text can describe a deletion as "tidy formatting". These
warnings are computed here from the action itself, deterministically, and
the model has no say in them. They don't block anything — the user's Apply
click is still the gate — they make a risky change hard to approve by accident.

    action_warnings(action, untrusted_seen) -> list[str]
"""

import os
import re

import config

# Files whose content shapes SLEEPY's behaviour on every later run, so a bad
# edit here persists instead of being a one-off.
_SENSITIVE_FILES = {
    "ABOUT.md": "describes you to the AI in every conversation",
    "People.md": "is the AI's reference for the people you know",
    "NewsWatch.md": "decides what the nightly news search looks for",
}
_RECUR_REASON = "creates tasks automatically on a schedule"

_URL_DOMAIN_RE = re.compile(r"https?://([^/\s)\]>\"']+)", re.IGNORECASE)
_LARGE_REMOVAL_LINES = 10


def _sensitive_reason(rel_path: str) -> str | None:
    if not rel_path:
        return None
    if rel_path in _SENSITIVE_FILES:
        return _SENSITIVE_FILES[rel_path]
    if "/Recur/" in f"/{rel_path}":
        return _RECUR_REASON
    return None


def _diff_lines(diff: str, sign: str) -> list[str]:
    header = sign * 3
    return [l[1:] for l in (diff or "").splitlines() if l.startswith(sign) and not l.startswith(header)]


def _existing_line_count(rel_path: str) -> int | None:
    try:
        with open(os.path.join(config.USER_DATA_ROOT, rel_path), encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


def action_warnings(action: dict, untrusted_seen: bool) -> list[str]:
    op = action.get("op", "write")
    warnings = []

    if untrusted_seen:
        warnings.append(
            "The AI read news from the web in this conversation before proposing this. "
            "Web pages can carry hidden instructions — make sure this is a change you asked for."
        )

    if op == "delete":
        warnings.append(f"Deletes {action.get('rel_path')} — it disappears from SLEEPY "
                        "(recoverable only from the corpus git history).")

    for path in (action.get("rel_path"), action.get("src_path"), action.get("dst_path")):
        reason = _sensitive_reason(path)
        if reason:
            warnings.append(f"Touches {path}, which {reason} — a bad change here keeps affecting SLEEPY.")
            break

    if op == "write" and not action.get("is_new"):
        removed = len(_diff_lines(action.get("diff", ""), "-"))
        if removed >= _LARGE_REMOVAL_LINES:
            total = _existing_line_count(action.get("rel_path", ""))
            share = f" of {total}" if total else ""
            warnings.append(f"Removes {removed}{share} existing lines.")

    if op == "write":
        domains = sorted({m.group(1).lower() for line in _diff_lines(action.get("diff", ""), "+")
                          for m in _URL_DOMAIN_RE.finditer(line)})
        if domains:
            warnings.append("Adds links to outside websites: " + ", ".join(domains[:5])
                            + (" …" if len(domains) > 5 else ""))

    return warnings
