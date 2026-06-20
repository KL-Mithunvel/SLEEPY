"""
News Watch — two-stage async news harvesting pipeline.

Stage 1 (midnight cron):  news_watch_submit_for_user(data_root)
  - Scans active projects for news_topics frontmatter
  - Applies day-of-week rotation (one project per day per 7-project cycle)
  - Submits one Anthropic Message Batch request per project×topic
  - Persists batch state to <data_root>/db/news_batch_state.json

Stage 2 (every 5 min):   news_watch_finalize_for_user(data_root)
  - Polls Anthropic batch status
  - On "ended": extracts bullet lines, runs URL dedup + LLM dedup
  - Appends surviving bullets to <data_root>/inbox.md under ## News
  - Updates <data_root>/db/news_seen.json

State files:
  <data_root>/db/news_seen.json       — dedup memory (capped at 1000 items)
  <data_root>/db/news_batch_state.json — inter-stage batch handoff
"""

import json
import logging
import os
import re
import urllib.parse
from datetime import date, datetime
from pathlib import Path

import yaml

import config

logger = logging.getLogger(__name__)

_BULLET_RE = re.compile(
    r"^\s*-\s+\[\s+\]\s+📰\s+\[.+?\]\(https?://.+?\).*$",
    re.MULTILINE,
)
_URL_RE = re.compile(r"\(https?://([^\)]+)\)")

_WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 2,
}

_MAX_SEEN = 1000
_MAX_ABOUT_CHARS = 6000


# ---------------------------------------------------------------------------
# State file helpers
# ---------------------------------------------------------------------------

def _db_dir(data_root: str) -> Path:
    p = Path(data_root) / "db"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_news_seen(data_root: str) -> list[dict]:
    path = _db_dir(data_root) / "news_seen.json"
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("news_seen.json corrupt — starting fresh")
        return []


def _save_news_seen(data_root: str, seen: list[dict]) -> None:
    path = _db_dir(data_root) / "news_seen.json"
    # Cap at 1000 items FIFO
    seen = seen[-_MAX_SEEN:]
    path.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_batch_state(data_root: str) -> dict | None:
    path = _db_dir(data_root) / "news_batch_state.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_batch_state(data_root: str, state: dict) -> None:
    path = _db_dir(data_root) / "news_batch_state.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Project scanning
# ---------------------------------------------------------------------------

_SKIP_DIRS = {"db", "logs", "archive", ".git", "Recur", "Plans", "Daily", "Govern"}


def _scan_active_projects(data_root: str) -> list[dict]:
    """
    Walk data_root and return info dicts for active project files.
    A project file must have a YAML frontmatter with status: active (or no status).
    """
    md_root = Path(data_root)
    projects = []

    for ou_dir in sorted(md_root.iterdir()):
        if not ou_dir.is_dir() or ou_dir.name in _SKIP_DIRS:
            continue
        for md_file in sorted(ou_dir.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
                if not (content.startswith("---\n") or content.startswith("---\r\n")):
                    continue
                end = content.find("\n---", 4)
                if end == -1:
                    continue
                try:
                    fm = yaml.safe_load(content[4:end]) or {}
                except yaml.YAMLError:
                    continue

                status = str(fm.get("status") or "").strip().lower()
                if status in ("archived", "complete", "done", "paused"):
                    continue

                project_key = str(fm.get("key") or md_file.stem)
                news_topics_raw = fm.get("news_topics")
                topics = _parse_topics(news_topics_raw)
                flag = str(fm.get("flag") or "").strip().lower()
                mtime = md_file.stat().st_mtime

                projects.append({
                    "key": project_key,
                    "title": fm.get("title") or md_file.stem,
                    "ou": ou_dir.name,
                    "file_path": str(md_file),
                    "content": content,
                    "topics": topics,
                    "flag": flag,
                    "mtime": mtime,
                })
            except Exception:
                logger.exception("Error scanning %s", md_file)

    return projects


def _parse_topics(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    if isinstance(raw, str):
        # comma-separated
        return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def _todays_project_keys(all_keys: list[str]) -> set[str]:
    if config.NEWS_RUN_ALL:
        return set(all_keys)
    today_dow = date.today().weekday()
    sorted_keys = sorted(all_keys)
    return {k for i, k in enumerate(sorted_keys) if i % 7 == today_dow}


def _item_budget(proj: dict) -> int:
    """How many news items to request per topic."""
    if proj["flag"] in ("star", "important", "urgent"):
        return 5
    recency_cutoff = datetime.now().timestamp() - config.RECENCY_DAYS * 86400
    if proj["mtime"] > recency_cutoff:
        return 5
    return 2


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _load_about(data_root: str) -> str:
    f = Path(data_root) / "ABOUT.md"
    if not f.is_file():
        return ""
    text = f.read_text(encoding="utf-8", errors="replace")
    if len(text) > _MAX_ABOUT_CHARS:
        # Truncate at last section boundary
        cut = text.rfind("\n## ", 0, _MAX_ABOUT_CHARS)
        text = text[:cut if cut > 0 else _MAX_ABOUT_CHARS]
    return text


def _already_presented_block(seen: list[dict]) -> str:
    recent = seen[-60:] if len(seen) > 60 else seen
    bullets = [s["bullet"] for s in recent]
    return "\n".join(bullets) if bullets else ""


def _feedback_blocks(seen: list[dict]) -> tuple[str, str]:
    liked = [s["bullet"] for s in seen if s.get("feedback") == "+1"]
    disliked = [s["bullet"] for s in seen if s.get("feedback") == "-1"]
    return "\n".join(liked[-20:]), "\n".join(disliked[-20:])


def _system_prompt(about: str, liked: str, disliked: str, presented: str) -> list[dict]:
    today_str = date.today().strftime("%Y-%m-%d")
    base = (
        "You are a news assistant. When given a project context and topic, "
        "use web_search to find recent, relevant news articles published within "
        f"the last 7 days (today is {today_str}). "
        "Return ONLY bullet items — one per article — in this EXACT format:\n\n"
        "- [ ] 📰 [Title of Article](https://full-url) — domain.com (YYYY-MM-DD) "
        "| *topic: <topic>* | *relevant because: <1-sentence reason>*\n\n"
        "Rules:\n"
        "- Every article MUST have a real URL (no made-up links).\n"
        "- Publication date MUST be within the last 7 days.\n"
        "- Do NOT output anything other than bullet lines.\n"
        "- Do NOT repeat items from the 'Already presented' list.\n"
        "- Prefer articles directly relevant to the project context."
    )
    blocks: list[dict] = [{"type": "text", "text": base}]
    if about:
        blocks.append({"type": "text", "text": f"## User Profile\n\n{about}"})
    if liked:
        blocks.append({
            "type": "text",
            "text": f"## Examples of items the user found RELEVANT (aim for similar quality)\n\n{liked}",
        })
    if disliked:
        blocks.append({
            "type": "text",
            "text": f"## Examples of items the user found IRRELEVANT (avoid similar)\n\n{disliked}",
        })
    if presented:
        blocks.append({
            "type": "text",
            "text": f"## Already presented (DO NOT repeat these)\n\n{presented}",
            "cache_control": {"type": "ephemeral"},
        })
    else:
        # Cache control on last block
        blocks[-1] = dict(blocks[-1])
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def _topic_user_msg(project_key: str, topic: str, project_content: str, max_items: int) -> list[dict]:
    ctx_block = {
        "type": "text",
        "text": f"## Project Context: {project_key}\n\n{project_content[:8000]}",
        "cache_control": {"type": "ephemeral"},
    }
    ask = (
        f"Find up to {max_items} recent news articles about "
        f"**{topic}** relevant to project {project_key}. "
        "Return only bullet lines in the required format."
    )
    return [
        {"role": "user", "content": [ctx_block, {"type": "text", "text": ask}]}
    ]


def _content_user_msg(project_key: str, project_content: str) -> list[dict]:
    ctx_block = {
        "type": "text",
        "text": f"## Project Context: {project_key}\n\n{project_content[:8000]}",
        "cache_control": {"type": "ephemeral"},
    }
    ask = (
        f"Based on the project context above, find 1 recent news item "
        f"(published within 7 days) that is directly relevant to project {project_key}. "
        "Return only 1 bullet line in the required format, or nothing if nothing is relevant."
    )
    return [
        {"role": "user", "content": [ctx_block, {"type": "text", "text": ask}]}
    ]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _normalise_url(url: str) -> str:
    try:
        p = urllib.parse.urlparse(url.lower().rstrip("/"))
        return urllib.parse.urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    except Exception:
        return url.lower()


def _bullet_key(bullet: str) -> tuple[str, str] | None:
    m = _URL_RE.search(bullet)
    if m:
        url = "https://" + m.group(1)
        return ("url", _normalise_url(url))
    return None


def _server_dedup(bullets: list[str], seen_keys: set) -> list[str]:
    result = []
    for b in bullets:
        k = _bullet_key(b)
        if k and k in seen_keys:
            continue
        result.append(b)
        if k:
            seen_keys.add(k)
    return result


def _llm_dedup(client, bullets: list[str], recent_seen: list[dict]) -> list[str]:
    """LLM semantic dedup pass. Failure-tolerant: returns all on error."""
    if not bullets or not recent_seen:
        return bullets
    try:
        seen_block = "\n".join(s["bullet"] for s in recent_seen[-100:])
        cand_block = "\n".join(f"{i+1}. {b}" for i, b in enumerate(bullets))
        prompt = (
            "Below are SEEN items (already presented to the user) and CANDIDATE items.\n"
            "Return a JSON array of 1-based indices of candidates that are near-duplicate "
            "of any seen item (same story, different outlet, or paraphrased headline).\n"
            "Return [] if none are duplicates.\n\n"
            f"SEEN:\n{seen_block}\n\nCANDIDATES:\n{cand_block}\n\n"
            "Reply with ONLY a JSON array, e.g. [2, 5]"
        )
        import llm as llm_module
        result = llm_module.chat(
            [{"role": "user", "content": prompt}],
            system="You are a deduplication assistant. Reply only with a JSON array.",
        )
        drop_indices = set(json.loads(result.text.strip()))
        return [b for i, b in enumerate(bullets, 1) if i not in drop_indices]
    except Exception:
        logger.warning("LLM dedup pass failed — keeping all candidates")
        return bullets


# ---------------------------------------------------------------------------
# Inbox writer
# ---------------------------------------------------------------------------

def _write_news_to_inbox(data_root: str, bullets: list[str]) -> None:
    inbox_path = Path(data_root) / "inbox.md"
    if inbox_path.is_file():
        content = inbox_path.read_text(encoding="utf-8", errors="replace")
    else:
        content = "# Inbox\n\n## News\n"

    if "## News" not in content:
        content += "\n## News\n"

    idx = content.find("## News")
    section_end = content.find("\n## ", idx + 1)
    insert_at = (idx + len("## News") + 1) if section_end == -1 else section_end

    bullet_block = "\n".join(bullets) + "\n"
    content = content[:insert_at] + bullet_block + content[insert_at:]
    inbox_path.write_text(content, encoding="utf-8")
    logger.info("Wrote %d news bullets to inbox.md", len(bullets))


# ---------------------------------------------------------------------------
# Stage 1: Submit
# ---------------------------------------------------------------------------

def news_watch_submit_for_user(data_root: str) -> dict:
    """
    Build and submit an Anthropic Message Batch for all due projects.
    Returns {"status": "submitted", "batch_id": ..., "request_count": ...}
    or {"status": "no_projects"} if nothing to do.
    """
    import anthropic as anthropic_module

    client = anthropic_module.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    seen = _load_news_seen(data_root)
    seen_keys = {
        k for s in seen
        for k in ([tuple(_bullet_key(s["bullet"]))] if _bullet_key(s["bullet"]) else [])
    }

    about = _load_about(data_root)
    liked, disliked = _feedback_blocks(seen)
    presented = _already_presented_block(seen)
    system_blocks = _system_prompt(about, liked, disliked, presented)

    projects = _scan_active_projects(data_root)
    all_keys = [p["key"] for p in projects]
    todays_keys = _todays_project_keys(all_keys)

    today_projects = [p for p in projects if p["key"] in todays_keys]
    if not today_projects:
        logger.info("news_watch_submit: no projects scheduled today")
        return {"status": "no_projects"}

    requests = []
    custom_id_map: dict[str, dict] = {}

    for proj in today_projects:
        budget = _item_budget(proj)
        if proj["topics"]:
            for ti, topic in enumerate(proj["topics"]):
                cid = f"{proj['key']}__t{ti}"
                custom_id_map[cid] = {"project_key": proj["key"], "topic": topic}
                requests.append({
                    "custom_id": cid,
                    "params": {
                        "model": config.LLM_DEFAULT_MODEL,
                        "max_tokens": 1024,
                        "system": system_blocks,
                        "messages": _topic_user_msg(proj["key"], topic, proj["content"], budget),
                        "tools": [_WEB_SEARCH_TOOL],
                    },
                })
        else:
            cid = f"{proj['key']}__content"
            custom_id_map[cid] = {"project_key": proj["key"], "topic": None}
            requests.append({
                "custom_id": cid,
                "params": {
                    "model": config.LLM_DEFAULT_MODEL,
                    "max_tokens": 512,
                    "system": system_blocks,
                    "messages": _content_user_msg(proj["key"], proj["content"]),
                    "tools": [_WEB_SEARCH_TOOL],
                },
            })

    if not requests:
        return {"status": "no_requests"}

    logger.info("news_watch_submit: submitting %d requests", len(requests))
    batch = client.beta.messages.batches.create(requests=requests)

    job_id = f"news-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    state = {
        "job_id": job_id,
        "batch_id": batch.id,
        "submitted_at": datetime.now().isoformat(),
        "request_count": len(requests),
        "custom_id_map": custom_id_map,
        "metadata": {
            "scanned": len(projects),
            "scheduled_keys": list(todays_keys),
            "weekday": date.today().weekday(),
        },
        "finalized": False,
    }
    _save_batch_state(data_root, state)
    logger.info("news_watch_submit: batch_id=%s job_id=%s", batch.id, job_id)
    return {"status": "submitted", "batch_id": batch.id, "job_id": job_id, "request_count": len(requests)}


# ---------------------------------------------------------------------------
# Stage 2: Finalize
# ---------------------------------------------------------------------------

def news_watch_finalize_for_user(data_root: str) -> dict:
    """
    Poll batch status. If ended, process results and write bullets to inbox.md.
    Returns status dict with processing info.
    """
    state = _load_batch_state(data_root)
    if not state:
        return {"status": "no_batch"}
    if state.get("finalized"):
        return {"status": "already_finalized", "batch_id": state.get("batch_id")}

    import anthropic as anthropic_module

    client = anthropic_module.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    batch_id = state["batch_id"]

    batch = client.beta.messages.batches.retrieve(batch_id)
    processing_status = batch.processing_status

    if processing_status != "ended":
        counts = batch.request_counts
        return {
            "status": "in_progress",
            "batch_id": batch_id,
            "processing_status": processing_status,
            "request_counts": {
                "processing": getattr(counts, "processing", 0),
                "succeeded": getattr(counts, "succeeded", 0),
                "errored": getattr(counts, "errored", 0),
                "canceled": getattr(counts, "canceled", 0),
                "expired": getattr(counts, "expired", 0),
            },
        }

    # Batch has ended — process results
    seen = _load_news_seen(data_root)
    seen_keys: set = set()
    for s in seen:
        k = _bullet_key(s["bullet"])
        if k:
            seen_keys.add(tuple(k))

    all_bullets: list[str] = []
    errored = 0
    custom_id_map: dict = state.get("custom_id_map", {})

    for result in client.beta.messages.batches.results(batch_id):
        if result.result.type != "succeeded":
            errored += 1
            continue
        message = result.result.message
        topic_info = custom_id_map.get(result.custom_id, {})
        topic = topic_info.get("topic")

        text = ""
        for block in message.content:
            if hasattr(block, "type") and block.type == "text":
                text += block.text

        for m in _BULLET_RE.finditer(text):
            bullet = m.group(0).strip()
            # Ensure topic annotation is present
            if topic and f"*topic: {topic}*" not in bullet:
                bullet += f" | *topic: {topic}*"
            all_bullets.append(bullet)

    # Server-side URL dedup
    deduped_key = len(all_bullets)
    surviving = _server_dedup(all_bullets, seen_keys)
    deduped_key -= len(surviving)

    # LLM semantic dedup
    deduped_llm = len(surviving)
    surviving = _llm_dedup(client, surviving, seen)
    deduped_llm -= len(surviving)

    if surviving:
        _write_news_to_inbox(data_root, surviving)

    # Update seen state
    today_str = date.today().strftime("%Y-%m-%d")
    for bullet in surviving:
        seen.append({"bullet": bullet, "key": str(_bullet_key(bullet)), "date": today_str, "feedback": None})
    _save_news_seen(data_root, seen)

    # Mark finalized
    state["finalized"] = True
    state["finalized_at"] = datetime.now().isoformat()
    state["result_counts"] = {
        "total": len(all_bullets),
        "deduped_key": deduped_key,
        "deduped_llm": deduped_llm,
        "appended": len(surviving),
        "errored": errored,
    }
    _save_batch_state(data_root, state)

    logger.info(
        "news_watch_finalize: appended=%d deduped_key=%d deduped_llm=%d errored=%d",
        len(surviving), deduped_key, deduped_llm, errored,
    )
    return {
        "status": "complete",
        "appended": len(surviving),
        "deduped_key": deduped_key,
        "deduped_llm": deduped_llm,
        "errored": errored,
    }


# ---------------------------------------------------------------------------
# Status query (for API)
# ---------------------------------------------------------------------------

def get_batch_status(data_root: str) -> dict:
    state = _load_batch_state(data_root)
    if not state:
        return {"status": "no_batch"}
    if state.get("finalized"):
        counts = state.get("result_counts", {})
        return {
            "status": "complete",
            "batch_id": state["batch_id"],
            "job_id": state.get("job_id"),
            **counts,
        }
    # Proxy to Anthropic for live status
    try:
        import anthropic as anthropic_module
        client = anthropic_module.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        batch = client.beta.messages.batches.retrieve(state["batch_id"])
        counts = batch.request_counts
        return {
            "status": "in_progress" if batch.processing_status != "ended" else "processing_results",
            "batch_id": state["batch_id"],
            "job_id": state.get("job_id"),
            "processing_status": batch.processing_status,
            "request_counts": {
                "processing": getattr(counts, "processing", 0),
                "succeeded": getattr(counts, "succeeded", 0),
                "errored": getattr(counts, "errored", 0),
                "canceled": getattr(counts, "canceled", 0),
                "expired": getattr(counts, "expired", 0),
            },
        }
    except Exception as exc:
        return {"status": "error", "batch_id": state.get("batch_id"), "error": str(exc)}


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def get_feedback(data_root: str) -> list[dict]:
    return _load_news_seen(data_root)


def submit_feedback(data_root: str, bullet: str, feedback: str) -> bool:
    """
    Update feedback (+1 / -1) for a bullet in news_seen.json.
    Returns True if found and updated, False otherwise.
    """
    seen = _load_news_seen(data_root)
    for entry in seen:
        if entry["bullet"].strip() == bullet.strip():
            entry["feedback"] = feedback
            _save_news_seen(data_root, seen)
            return True
    return False
