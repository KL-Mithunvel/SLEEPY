# PMA News Watch System — Complete Reference

## Overview

News Watch is PMA's daily per-project news harvesting system. It walks every active project in the user's corpus, asks Claude to web-search recent items, filters them through the project's full file content, and writes one bullet per relevant story to `inbox.md` under `## News`.

Two paths per project:
- **With `news_topics:` frontmatter** — one Anthropic batch request per topic, item budget of 5 (flagged/recent) or 2 (low-priority) per topic
- **Without `news_topics:`** — one content-grounded request per project, capped at 1 item, biased toward `(none)`. The day-of-week rotation means each topic-less project is visited about once a week

## Architecture: Two-Stage Async Pipeline

### Stage 1: Submit (`news_watch_submit_for_user`)
Called by worker at midnight (00:00), or on-demand via `POST /api/corpus/news-watch`.

1. Loads dedup memory from `<user>/db/news_seen.json`
2. Loads user profile `<md_root>/ABOUT.md` for relevance grounding (up to 6000 chars)
3. Loads 👍/👎 feedback signals from `news_seen.json`
4. Scans all active project files (status: active) for `news_topics:` frontmatter
5. Applies day-of-week rotation to select today's projects
6. Builds batch request payload (one request per project×topic)
7. Submits to Anthropic Message Batches API
8. Saves batch state to `<user>/db/news_batch_state.json`

### Stage 2: Finalize (`news_watch_finalize_for_user`)
Called by `news_watch_poll_job` every 5 minutes (and on-demand status checks).

1. Reads `news_batch_state.json` to find pending batch ID
2. Calls Anthropic batch retrieve API
3. If `processing_status != "ended"`: returns `{"status": "in_progress"}`
4. If ended: fetches result pages, extracts bullet lines
5. Server-side dedup (URL-based key matching)
6. LLM dedup pass (semantic near-duplicate removal)
7. Appends surviving bullets to `<md_root>/inbox.md` under `## News`
8. Persists seen state to `news_seen.json`
9. Marks batch state as `finalized: true`

## Persistent State Files

### `<user>/db/news_seen.json`
Durable memory — survives inbox clearing. Capped at 1000 items, FIFO.
```json
[
  {
    "bullet": "- [ ] 📰 [Title](URL) — domain.com (2026-06-18)",
    "key": "url:https://domain.com/article/path",
    "date": "2026-06-18",
    "feedback": null  // null | "+1" | "-1"
  }
]
```

### `<user>/db/news_batch_state.json`
Inter-stage state between submit and finalize:
```json
{
  "batch_id": "msgbatch_01...",
  "submitted_at": "2026-06-18T00:00:05",
  "request_count": 7,
  "custom_id_map": {
    "PROJ-IT__t0": {"project_key": "PROJ-IT", "topic": "cloud infrastructure"},
    "PROJ-IT__t1": {"project_key": "PROJ-IT", "topic": "kubernetes"}
  },
  "metadata": {
    "scanned": 5,
    "with_topics_keys": ["PROJ-IT", "PROJ-MKT"],
    "no_topics_keys": ["PROJ-MISC"],
    "deferred_keys": ["PROJ-OLD"],
    "topics_total": 6,
    "weekday": 2
  },
  "finalized": false
}
```

## Configuring News Watch for a Project

### In Project File Frontmatter
```yaml
---
key: PROJ-IT
title: Infrastructure Modernisation
status: active
flag: important        # star | important | urgent → gets 5 items/topic
news_topics:
  - kubernetes security
  - cloud cost optimisation
  - AWS infrastructure
  - DevSecOps
---
```

### Frontmatter Fields Affecting News Watch

| Field | Effect |
|-------|--------|
| `status: active` | Only active projects get news (not paused, complete, archived) |
| `flag: star/important/urgent` | Gets 5 items per topic (vs 2 for normal) |
| `news_topics: [...]` | List of topics to search — one batch request per topic |
| No `news_topics:` | Gets 1 content-grounded request per week (day-of-week rotation) |

### `news_topics:` Formats
All three forms are accepted:
```yaml
# List form (preferred)
news_topics:
  - kubernetes security
  - AWS cost

# Comma-separated shorthand
news_topics: kubernetes security, AWS cost

# Single topic
news_topics: kubernetes
```

### Item Budget Per Topic
- **Flagged projects** (flag: star/important/urgent): 5 items per topic
- **Recently edited projects** (edited within 14 days): 5 items per topic
- **Other projects**: 2 items per topic
- **Topic-less projects**: 1 item (content-grounded, not per-topic)

## Day-of-Week Rotation

To control costs, news watch runs each project once per week on a rotating schedule:

```python
def _todays_project_keys(all_keys: list[str]) -> set[str]:
    today_dow = date.today().weekday()  # Mon=0 ... Sun=6
    sorted_keys = sorted(all_keys)      # alphabetical sort
    return {k for i, k in enumerate(sorted_keys) if i % 7 == today_dow}
```

- Projects sorted alphabetically by key
- Project at index `i` runs on weekday `i % 7`
- With 7 projects: each runs once/week
- With 14 projects: each runs twice/week (indices 0,7 on Monday, 1,8 on Tuesday, etc.)
- Override: `PMA_NEWS_RUN_ALL=1` env var → all projects run every day

## Output Format: News Bullets

Each news item is written as a bullet to `inbox.md` under `## News`:

```markdown
## News

- [ ] 📰 [Title of Article](https://source.com/article) — source.com (2026-06-18) | *topic: kubernetes security* | *relevant because: cluster CVE affects your PROJ-IT architecture*
```

Format breakdown:
- `- [ ]` — task checkbox (user can check when read/actioned)
- `📰` — news emoji (distinguishes from other inbox items)
- `[Title](URL)` — markdown link
- `— domain.com (YYYY-MM-DD)` — source and date
- `| *topic: ...*` — which news_topics entry triggered this
- `| *relevant because: ...*` — AI's relevance explanation

## Deduplication: Three Layers

### Layer 1: `already_presented` in Prompt
The most recent 60 bullets from `news_seen.json` are included in the system prompt as "already presented" context. Claude tries to avoid surfacing the same stories.

This layer is probabilistic (LLM-based) — the next two layers are deterministic backstops.

### Layer 2: Server-Side Key Dedup
Each bullet gets a stable key:
- **URL-based key** (preferred): `("url", "<normalised-URL>")` — same article from same site under different headline → same key
- **Fallback key**: `("dom", "<domain>", "<date>", "<head60>")` — domain + date + first 60 chars of headline, lowercased

URL normalisation:
- Lowercase hostname
- Strip query string and fragment
- Strip trailing slash

Any bullet whose key matches something in `news_seen.json` is dropped.

### Layer 3: LLM Dedup Pass
After server-side dedup, a final synchronous Claude call compares remaining candidates against the 100 most recent seen items and flags semantic near-duplicates (same story, different outlet, paraphrased headline).

Returns a JSON array of 1-based indices to drop. Failure-tolerant: any error → keep all candidates.

## Prompt Caching in News Watch

Two `cache_control: {type: "ephemeral", ttl: "1h"}` breakpoints per batch request:

1. **After `already_presented` block** — covers: system rules + ABOUT.md + feedback signal + already-presented list. Shared across ALL requests in the batch.
2. **After `project_context` block** — covers: per-project content. Shared across multiple topics of the same project.

TTL is 1 hour (not 5 minutes like chat) because batch processing is unpredictable (typically minutes, can stretch to hours).

## ABOUT.md — User Profile for Relevance Grounding

`<md_root>/ABOUT.md` is loaded for every news watch run to help Claude understand the user's context:

```markdown
# About Me

I run IT infrastructure for a mid-size engineering firm. Key areas:
- Cloud infrastructure: AWS primarily, some GCP
- Team: 8 people, mostly senior engineers
- Focus in 2026: cost reduction + security posture
...
```

- Read from `<md_root>/ABOUT.md` (not per-OU, corpus root)
- Truncated at 6000 chars at last section boundary
- If missing: no error, just omitted from prompt

## Feedback System

Users can rate news items 👍/👎 from the frontend (Settings or Today view).

### API
- `GET /api/corpus/news-feedback` — list all feedback (with current ratings)
- `POST /api/corpus/news-feedback` — submit rating
  ```json
  {"bullet": "- [ ] 📰 [Title](URL)...", "feedback": "+1"}
  ```

### How Feedback Affects Future Runs
Feedback is stored in `news_seen.json` alongside each bullet. On the next run:
- **Liked items (`+1`)**: A `liked_block` is built from all +1 bullets and included in the system prompt as "examples of items the user found relevant — aim for similar quality"
- **Disliked items (`-1`)**: A `disliked_block` is built and included as "examples of items the user found irrelevant or low-quality — avoid similar items"

This creates a learning loop without fine-tuning: feedback shapes the system prompt context on every subsequent run.

## On-Demand News Watch

Users can trigger news watch for a specific OU from the UI:

### `POST /api/corpus/news-watch`
```json
{"ou": "PROJ-IT"}
```
Response:
```json
{"job_id": "news-20260618-120530", "status": "submitted"}
```

### `GET /api/corpus/news-watch/status/<job_id>`
Two-stage status:
```json
{
  "status": "in_progress",        // submitted | in_progress | complete | failed
  "batch_id": "msgbatch_01...",
  "processing_status": "in_progress",  // Anthropic's status
  "request_counts": {
    "processing": 3,
    "succeeded": 4,
    "errored": 0,
    "canceled": 0,
    "expired": 0
  }
}
```

When complete:
```json
{
  "status": "complete",
  "appended": 5,
  "deduped_key": 2,
  "deduped_llm": 1,
  "errored": 0
}
```

## Configuration Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PMA_NEWS_WATCH_CRON_DISABLED` | False | Set to 1 to disable nightly auto-run |
| `PMA_NEWS_RUN_ALL` | False | Set to 1 to run all projects every day (ignores day-of-week rotation) |
| `NEWS_MAX_RETRIES` | 8 | Anthropic SDK retries on batch-create and LLM-dedup calls |
| `RECENCY_DAYS` | 14 | Projects edited within this many days are considered "recently active" → get 5 items/topic |

## Web Search Tool Used

```python
_WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 2,      # Claude fires at most 2 searches per request
}
```

Claude executes the web search server-side and synthesises the result — PMA doesn't handle raw search results.

## Prompt Templates

All prompts live in `code/src/prompts/news_watch.md.j2` (Jinja2 template). Macros:
- `system()` — core instructions: format, constraints, recency requirement, bullet format
- `about(text)` — user profile section
- `liked(block)` — positive feedback examples
- `disliked(block)` — negative feedback examples
- `presented(block)` — already-seen items (dedup hint)
- `project(context)` — project file content
- `topic_user(key, topic, max_items)` — per-topic user message
- `topic_none_user(key)` — topic-less project user message
- `dedup_pass(seen_block, seen_count, cand_block, cand_count)` — LLM dedup prompt

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| Anthropic batch API down | Exception raised, logged, batch not submitted. Retry on next nightly run. |
| Batch request expires | Anthropic expires batches after 24h. `finalize` catches this, marks finalized. |
| Individual request errored | Skipped in finalize; errored count logged. Other requests still processed. |
| LLM dedup pass fails | All candidates kept (fail-open). Logged as warning. |
| `news_seen.json` corrupt | Fresh start (empty dedup memory). Logged as warning. |
| `ABOUT.md` missing | Silently omitted from prompt. |
| `inbox.md` missing | Created fresh with `## News` section. |
