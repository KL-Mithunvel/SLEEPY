# PMA Pending Features & TODO

## Current Version
`0.1.41` (as of June 2026)

## Pending Features (from `TODO.md`)

### High Priority / Near-term

#### Email / Telegram Follow-up from Govern
- Govern view shows team tasks and assignments
- Need: ability to send follow-up email or Telegram message directly from Govern view
- Status: not yet implemented
- Related: email handler and telegram handler exist in task_queue; just needs UI + AI tool usage from Govern context

#### Overdue Task Nudge
- Automatic detection of overdue tasks (due date passed, still `- [ ]`)
- Proactive notification via Telegram or morning briefing
- Status: not yet implemented
- Related: materialiser has access to all plans files; worker could run daily check

#### Migrate Task Toggle to Line-Edit API
- Currently task checkboxes use full file PUT (read → modify → write entire file)
- Better: use `POST /api/corpus/line-edit` for atomic line-level toggle
- Avoids race conditions with concurrent edits
- Status: planned but not migrated

#### MCP Dedicated Keycloak Client
- Currently MCP OAuth reuses the main `pma` Keycloak client
- Should have dedicated client (`MCP_KEYCLOAK_CLIENT_ID`) for proper scope separation
- Status: `MCP_KEYCLOAK_CLIENT_ID` config var exists but not fully wired

### Medium Priority

#### Git Remote Push
- MD corpus git repo has no remote configured by default
- Need: automated push to remote (GitHub, GitLab, etc.) for off-machine backup
- Planned approach: new `git_push_job` in worker.py running nightly
- Status: not yet implemented
- Mentioned: can be done manually today

#### FTS5 Content Search
- Current `/api/corpus/search` does literal substring match (grep)
- Better: SQLite FTS5 full-text search for better relevance and performance
- Would complement (not replace) ChromaDB semantic search
- Status: not yet implemented

#### PWA Re-introduction
- Progressive Web App support was deferred
- Needed for: offline access, home screen install, push notifications
- Status: service worker removed, manifest not configured

### Lower Priority / Deferred

#### Project Digest / Pattern Reflection
- "Second-brain" layer for cross-project pattern detection
- Periodic digest: "Here are themes I'm noticing across your projects"
- Status: news watch implemented; project digest deferred

#### Multi-User Onboarding
- PMA is designed for single user (self-hosted personal assistant)
- Multi-user: needs automated user provisioning (create DATA_ROOT/<user>/ on first login)
- Potentially: admin UI for managing users
- Status: per-user data isolation already built in; just needs onboarding flow

#### Calendar Integration
- Read calendar events → include in morning briefing / today view
- Planned: Google Calendar API or Office 365 Calendar via Graph API
- Status: not yet started

#### Weather Station Integration
- Pull local weather data → include in morning briefing
- Status: not yet started

#### Second-Brain Layer (Long-term)
- Cross-conversation memory and pattern recognition
- Weekly/monthly digest of patterns, themes, recurring concerns
- Status: news watch is step 1; full second-brain layer deferred

## Architecture Decisions Made (Won't Change)

These are settled design decisions that inform the above features:

1. **No LLM in materialiser** — materialisation is deterministic, testable, fast
2. **pma-edit format** — SEARCH/REPLACE over unified diff (simpler, less error-prone)
3. **Per-user ChromaDB** — isolation, no cross-user data leakage
4. **Markdown + Git as SoT** — all user data in version-controlled markdown
5. **Separate worker process** — APScheduler in own container, not in Flask app
6. **uv as package manager** — reproducible builds with lockfile
7. **Prompt caching** — cost reduction on repeated long system prompts
8. **Dual-venv isolation** — app deps and tooling deps strictly separate

## Known Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|-----------|
| SQLite task queue | Single-machine only, no distributed processing | Acceptable for personal use |
| ChromaDB embedded | Single-machine only, not clustered | Acceptable for personal use |
| No git remote auto-push | Corpus backup requires manual setup | Documented in deployment guide |
| Single OU context per chat | Can't easily work across OUs in one session | Switch OU manually via frontend |
| APScheduler single-threaded | Jobs run sequentially, slow job delays others | Jobs are generally fast |
| No real-time multi-device sync | Changes on one device not immediately reflected on another | Git commit hourly provides eventual sync |
| Archive detection is heuristic | May miss some retrospective queries | LLM can explicitly call `search_corpus(include_archive=True)` |
