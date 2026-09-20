# RECOVERY.md — what breaks, what fixes itself, what you do

Added 2026-09-19 alongside the alerting / offsite / self-heal work.

This is the runbook for when something in SLEEPY goes wrong. It assumes the
two-branch deploy model in `.claude/CLAUDE.md` (`main` = dev, `prod` = live)
and the no-unauthorized-prod-access rule: read-only checks on the box are
always fine, anything that mutates state needs explicit go-ahead.

---

## 1. What recovers without you

Most failures are already handled. Before doing anything by hand, check
whether it is on this list.

| Failure | What happens | Where |
|---|---|---|
| A container crashes or the box reboots | Docker restarts it | `restart: unless-stopped` |
| A background task raises | Retried with exponential backoff (30s·2ⁿ) up to `max_attempts` | `task_queue.mark_failed` |
| The worker is killed mid-task | Row reclaimed after `LOCK_MINUTES` (30) | `task_queue.claim_next` |
| The worker is stopped cleanly mid-task | Task handed straight back, attempt refunded | `task_queue.release` |
| A handler dies half-written | Rolled back before the failure is recorded | `worker._drain_once` |
| Midnight materialise missed (box was off) | Re-run at worker startup, and by `self_check` if today has no Daily file | `worker.main`, `selfheal.check_todays_daily` |
| 06:30 briefing missed | Regenerated on first page load that day | `today_bp.get_today` |
| A task exhausts its retries | Email alert, then one automatic requeue if it is idempotent | `alerts.task_failed`, `selfheal.check_failed_tasks` |
| Abandoned `.git/index.lock` after a hard kill | Cleared at startup and every 15 min | `md_editor.clear_stale_git_locks` |
| Vector index empty while the corpus has files | `md_reindex` enqueued automatically | `selfheal.check_vector_index` |
| Gunicorn thread wedges | The arbiter kills and replaces the worker at `--timeout 120` | `Dockerfile.backend` |

**Not automatic, by design:** a corrupt SQLite file, a wedged-but-running
backend container, and any failed `email` / `morning_briefing` /
`news_watch_*` task. Each of those either needs a decision or could duplicate
a side effect. They alert instead.

---

## 2. When you get an alert email

All alerts come from `alerts.py`, subject prefixed `[SLEEPY]`, throttled to
one per `ALERT_COOLDOWN_HOURS` (default 6) per alert key. Every alert, sent
or suppressed, is in the `system_alerts` table:

```sql
SELECT created_at, alert_key, suppressed, subject FROM system_alerts
ORDER BY id DESC LIMIT 20;
```

| Alert | Meaning | Do this |
|---|---|---|
| `Background task failed: <type>` | A job burned through its retries | Check `last_error` in `task_queue`. Idempotent types get one auto-retry; others need you. |
| `Self-check: failed_tasks` | Failed jobs that are not safe to auto-retry | Decide per job. For `email`, confirm whether the message actually went out before re-sending. |
| `Self-check: vector_index` | ChromaDB unreachable | `docker compose ps`; restart the `chromadb` service. Index rebuilds itself afterwards. |
| `Self-check: disk_space` | Past `DISK_ALERT_PERCENT` (85%) | `docker image prune -af` first; then check `db/backups/`. |
| `Self-check: db_integrity` | `PRAGMA quick_check` failed | **Restore from backup — section 4.** Nothing repairs this automatically. |
| `Self-check: backend_health` | Worker is alive, web process is not answering | `docker compose restart backend`. |
| `offsite_push` failed | The nightly offsite copy did not happen | Section 3. Until fixed, there is no offsite copy of that day. |

---

## 3. Offsite replication — one-time setup

**Status: armed 2026-09-20.** The corpus pushes nightly to the private repo
`git@github.com:KL-Mithunvel/sleepy-corpus.git` (branch `master`), using the
deploy key described in step 2, with `OFFSITE_PUSH_ENABLED=1` set in the
worker's compose environment. First real push verified the same day: remote
`HEAD` matched local `master` exactly. The SQLite-snapshot half is still off
(`OFFSITE_SNAPSHOT_DIR` unset) — see step 5.

The steps below are the original one-time setup, kept for rebuilding from
scratch or repointing at a different remote. Before any remote exists,
`offsite_push` runs in "auto" mode and skips quietly every night: nothing is
offsite. Note that once a remote *does* exist, "auto" attempts the push for
real — a broken remote then fails loudly rather than skipping.

1. Create an **empty private** repo for the corpus (GitHub, Gitea, anywhere
   reachable over SSH). It holds your notes — private, always.
2. Create a deploy key with **write** access. The keypair already exists on
   the box at `~/sleepy/secrets/offsite_ed25519` (generated 2026-09-20,
   `chmod 600`, gitignored via `/secrets/`) — register the **`.pub` half** as
   the repo's deploy key, with "Allow write access" ticked. Regenerate with:
   ```bash
   ssh-keygen -t ed25519 -N '' -C 'sleepy-offsite-corpus' -f ~/sleepy/secrets/offsite_ed25519
   ```
   `docker-compose.yml` already mounts it read-only into the worker at
   `/app/secrets/offsite_ed25519` and sets `OFFSITE_SSH_KEY_PATH` to that
   path, so there is nothing to change in `secrets_app.py`. The file must
   exist on the host *before* `docker compose up`, or Docker creates a
   directory at that path instead. The worker image ships `openssh-client`
   for this — git shells out to the `ssh` binary to push.
3. Add the remote to the corpus repo on the box (not this code repo):
   ```bash
   git -C /home/ec2-user/sleepy/data/klm remote add origin <url>
   ```
4. Set `OFFSITE_PUSH_ENABLED=1` in the worker's `environment:` block in
   `docker-compose.yml` (env wins over `secrets_app.py`). With `"1"` a missing
   or broken remote becomes a hard failure that emails you, instead of a
   silent skip — which is what you want once you are relying on it.
5. Optionally set `OFFSITE_SNAPSHOT_DIR` to a **mounted remote** (rclone, S3,
   NFS) for the gzipped SQLite snapshots. A plain local path only protects
   against a bad write, not a lost volume.
6. Verify, without waiting for 03:30:
   ```bash
   docker compose exec worker uv run python -c \
     "import sys; sys.path.insert(0,'code/backend'); import offsite; print(offsite.run_offsite_push())"
   ```
   Then confirm the commits actually landed in the remote.

---

## 4. Restoring the SQLite database

Backups: `data/klm/db/backups/pma-YYYYMMDD-HHMM.db`, written nightly at 03:15
by `db_backup`, kept `DB_BACKUP_RETENTION_DAYS` (14) days. They are written
with SQLite's online backup API, so they are never torn mid-transaction.

**Verified end to end on 2026-09-19** (backup → gzip snapshot → restore →
`PRAGMA quick_check` = ok, migrations at v9, all rows intact).

```bash
# 1. Stop anything writing to it.
docker compose stop backend worker

# 2. Pick a backup and check it BEFORE trusting it.
ls -la data/klm/db/backups/
sqlite3 data/klm/db/backups/pma-20260919-0315.db "PRAGMA quick_check;"   # expect: ok

# 3. Keep the broken file — never overwrite it, it may be the only copy
#    of whatever happened after the last good backup.
mv data/klm/db/sqlite/pma.db data/klm/db/sqlite/pma.db.broken-$(date +%Y%m%d-%H%M)
rm -f data/klm/db/sqlite/pma.db-wal data/klm/db/sqlite/pma.db-shm

# 4. Restore and start.
cp data/klm/db/backups/pma-20260919-0315.db data/klm/db/sqlite/pma.db
docker compose start backend worker
curl -sf http://localhost:5000/healthz
```

From a gzipped offsite snapshot, prepend:
`gunzip -c pma-20260919-0315.db.gz > pma-20260919-0315.db`

**What you lose:** everything written since that backup — `ai_events` history,
queue history, login events. **What you do not lose:** the corpus. It is a git
repo, entirely independent of SQLite.

---

## 5. Restoring the corpus

The corpus is the only thing that is not rebuildable, so it has the most
recovery paths.

**A bad edit (most common).** Every AI write and every nightly job commits, so:

```bash
cd data/klm
git log --oneline -20
git diff HEAD~1                 # see what changed
git revert <sha>                # undo one commit, keeping history
git checkout <sha> -- path/to/file.md   # or restore one file
```

**The whole corpus is gone (volume lost, box destroyed).** Only possible if
section 3 was done:

```bash
git clone <corpus-remote-url> data/klm
mkdir -p data/klm/db/sqlite
# restore SQLite per section 4, then rebuild the index:
docker compose exec worker python -c \
  "import sys; sys.path.insert(0,'code/backend'); import local_db, md_indexer; \
   local_db.init_db(); c=local_db.get_db(); print(md_indexer.index_all(c), 'chunks')"
```

---

## 6. Rebuilding ChromaDB

Always safe — it is derived data, and losing it costs nothing but the time to
re-index.

```bash
docker compose restart chromadb
# then force a full reindex
docker compose exec worker python -c \
  "import sys; sys.path.insert(0,'code/backend'); import local_db, md_indexer; \
   local_db.init_db(); c=local_db.get_db(); print(md_indexer.index_all(c), 'chunks')"
```

`self_check` also enqueues `md_reindex` on its own whenever the index is empty
while the corpus has files.

---

## 7. Rebuilding the whole box

1. New EC2 instance, Docker + Docker Compose, nginx + certbot for
   `klm.smtw.in` (`tooling/nginx-klm.smtw.in.conf`).
2. `git clone <this repo>` and `git checkout prod`.
3. `git clone <corpus remote> data/klm` (section 3).
4. Recreate `code/backend/secrets_app.py` from
   `example_secrets_app.py` — **it is gitignored and in no backup**. Keep a
   copy somewhere you control, such as a password manager.
5. Restore SQLite (section 4) if you have a snapshot; otherwise `init_db()`
   creates a fresh schema and `manage_users.py create-user` recreates the
   accounts.
6. `docker compose build && docker compose up -d`, then `/healthz`.
7. Index rebuilds itself on the next `index_sync`.

**Without step 3 having been set up in advance, step 3 is impossible.** That
is the whole reason section 3 exists.

---

## 8. Stopping and starting

**Dev (Windows).** `tooling/stop-sleepy.ps1` drops a stop sentinel that
`main.py` and `worker.py` both watch, so they shut down through their normal
teardown — finishing the current task and releasing locks. Force is only the
fallback for anything still alive after 20s. Start with the Sleepy shortcut or
`tooling/run-backend.bat`.

**Prod.** `docker compose restart <service>`, or `up -d` after a rebuild. The
worker handles SIGTERM: it finishes the task in flight, hands it back to the
queue, and stops the scheduler. `tooling/prod-status.sh` is read-only and safe
to run any time; `tooling/deploy-prod.sh` does the full deploy with a health
gate.

**Never** `git reset --hard` on `main` or `prod`, and never force-push either.
Rollback is `git checkout <previous prod commit>` on the box, then rebuild.
