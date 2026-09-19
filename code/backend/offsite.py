"""
Offsite replication of the two things that cannot be rebuilt.

Everything the system owns lives on one EBS volume on one EC2 instance: the
MD corpus git repo, the nightly SQLite backups, the vector index. Chroma is
rebuildable and SQLite is mostly replaceable, but the corpus is the single
source of truth — losing that volume loses it outright. This is "Layer 5 —
Backup & recovery" from docs/SECURITY_REVIEW.md, which planned a nightly push
to a private remote and was never built.

Two independent targets, each skipped cleanly when unconfigured:

1. Corpus git push — `git push <remote> <branch>` of data/klm's own repo.
   Full history, offsite, one command. Default mode is "auto": push when the
   configured remote exists, stay quiet when it does not, so adding the remote
   on the box is the only step needed to switch this on. Setting
   OFFSITE_PUSH_ENABLED=1 explicitly makes a missing remote a hard failure
   instead — "I expect this to work, tell me when it does not".

2. SQLite snapshot copy — gzips the newest db/backups/pma-*.db into
   OFFSITE_SNAPSHOT_DIR and prunes by the same retention as the local backups.
   Point that at a mounted remote (rclone/S3/NFS) for it to be genuinely
   offsite; a plain local path only protects against a bad write, not a lost
   volume.

The SQLite snapshot deliberately does NOT ride along in the corpus git push:
git history is permanent, so a few MB of binary DB per night would grow the
repo without bound on a disk that is already tight.

Public API:
    push_corpus(data_root=None) -> dict
    push_snapshot() -> dict
    run_offsite_push(data_root=None) -> dict
"""

import datetime
import gzip
import logging
import os
import shutil

import config

logger = logging.getLogger(__name__)

# git push can hang indefinitely on an unreachable host or a credential
# prompt; this is a background job on a single-worker box, so it gets a hard
# ceiling.
PUSH_TIMEOUT_SEC = 120


def _mode() -> str:
    """Returns "auto" (push only if the remote exists), "on", or "off"."""
    raw = str(config.OFFSITE_PUSH_ENABLED).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return "on"
    if raw in ("0", "false", "no", "off"):
        return "off"
    return "auto"


# ---------------------------------------------------------------------------
# 1. Corpus git push
# ---------------------------------------------------------------------------

def push_corpus(data_root: str | None = None) -> dict:
    """
    Push the corpus repo to its configured remote.

    Returns a result dict with "pushed" and a human-readable "reason". Raises
    only when offsite push is explicitly enabled and something it was told to
    do could not be done — that failure is what turns into an alert email.
    """
    import git
    import md_editor

    root = data_root or config.USER_DATA_ROOT
    mode = _mode()
    remote_name = config.CORPUS_GIT_REMOTE

    if mode == "off":
        return {"pushed": False, "reason": "disabled"}

    try:
        repo = git.Repo(root, search_parent_directories=False)
    except git.InvalidGitRepositoryError:
        msg = f"no git repo at {root}"
        if mode == "on":
            raise RuntimeError(f"offsite push enabled but {msg}")
        logger.info("offsite: %s, skipping corpus push", msg)
        return {"pushed": False, "reason": msg}

    if remote_name not in [r.name for r in repo.remotes]:
        msg = f"corpus repo has no remote named {remote_name!r}"
        if mode == "on":
            raise RuntimeError(
                f"offsite push enabled but {msg} — add one with: "
                f"git -C {root} remote add {remote_name} <url>"
            )
        logger.info("offsite: %s, skipping corpus push (mode=auto)", msg)
        return {"pushed": False, "reason": msg}

    branch = config.CORPUS_GIT_BRANCH or _current_branch(repo)
    if not branch:
        msg = "corpus repo is in a detached HEAD state"
        if mode == "on":
            raise RuntimeError(f"offsite push enabled but {msg}")
        return {"pushed": False, "reason": msg}

    # Serialised against the hourly commit_pending job and any live AI edit —
    # pushing mid-commit would either block on git's own index.lock or ship a
    # half-written state.
    with md_editor.corpus_git_lock(root):
        with repo.git.custom_environment(**_git_env()):
            repo.git.push(remote_name, f"{branch}:{branch}", **_push_timeout_kwargs())

    logger.info("offsite: pushed corpus %s -> %s/%s", root, remote_name, branch)
    return {"pushed": True, "reason": "ok", "remote": remote_name, "branch": branch}


def _push_timeout_kwargs() -> dict:
    """
    GitPython raises outright on kill_after_timeout under Windows, so the
    hang-guard is applied only where it is supported. That is prod (Linux),
    which is the environment that actually runs this unattended at 03:30 —
    on the Windows dev box a push is always someone watching a test run.
    """
    if os.name == "nt":
        return {}
    return {"kill_after_timeout": PUSH_TIMEOUT_SEC}


def _current_branch(repo) -> str:
    try:
        return repo.active_branch.name
    except TypeError:       # detached HEAD
        return ""


def _git_env() -> dict:
    """
    Non-interactive git environment for an unattended push.

    BatchMode and GIT_TERMINAL_PROMPT=0 stop a missing credential turning into
    a background process blocked forever on a prompt nobody can answer — it
    fails fast and alerts instead.
    """
    key = config.OFFSITE_SSH_KEY_PATH
    ssh_cmd = "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
    if key:
        ssh_cmd += f" -i {key}"
    return {"GIT_SSH_COMMAND": ssh_cmd, "GIT_TERMINAL_PROMPT": "0"}


# ---------------------------------------------------------------------------
# 2. SQLite snapshot copy
# ---------------------------------------------------------------------------

def push_snapshot() -> dict:
    """
    Gzip the newest local SQLite backup into OFFSITE_SNAPSHOT_DIR and prune
    that directory by DB_BACKUP_RETENTION_DAYS. No-op when unconfigured.
    """
    import local_db

    dest_dir = config.OFFSITE_SNAPSHOT_DIR
    if not dest_dir:
        return {"copied": False, "reason": "OFFSITE_SNAPSHOT_DIR not configured"}

    db_dir = os.path.dirname(local_db.db_path())                    # .../db/sqlite
    backup_dir = os.path.join(os.path.dirname(db_dir), "backups")   # .../db/backups
    newest = _newest_backup(backup_dir)
    if newest is None:
        # db_backup runs at 03:15 and this at 03:30; an empty directory means
        # that job has not succeeded even once, which is itself worth knowing.
        raise RuntimeError(f"no SQLite backup found in {backup_dir} to replicate offsite")

    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(newest) + ".gz")
    with open(newest, "rb") as src, gzip.open(dest, "wb") as dst:
        shutil.copyfileobj(src, dst)

    pruned = _prune_snapshots(dest_dir)
    logger.info("offsite: snapshot %s -> %s (pruned %d)", newest, dest, pruned)
    return {"copied": True, "reason": "ok", "dest": dest, "pruned": pruned}


def _newest_backup(backup_dir: str) -> str | None:
    if not os.path.isdir(backup_dir):
        return None
    candidates = [
        os.path.join(backup_dir, n) for n in os.listdir(backup_dir)
        if n.startswith("pma-") and n.endswith(".db")
    ]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _prune_snapshots(dest_dir: str) -> int:
    cutoff = datetime.datetime.now() - datetime.timedelta(days=config.DB_BACKUP_RETENTION_DAYS)
    pruned = 0
    for name in os.listdir(dest_dir):
        if not (name.startswith("pma-") and name.endswith(".db.gz")):
            continue
        path = os.path.join(dest_dir, name)
        try:
            if datetime.datetime.fromtimestamp(os.path.getmtime(path)) < cutoff:
                os.remove(path)
                pruned += 1
        except OSError:
            logger.warning("offsite: could not prune %s", path)
    return pruned


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------

def run_offsite_push(data_root: str | None = None) -> dict:
    """
    Run both targets. Each is attempted independently so a broken remote does
    not stop the snapshot copy (or vice versa), but any failure is re-raised
    once both have had their turn — the task then retries, and alerts if it
    keeps failing.
    """
    results: dict = {}
    errors: list[str] = []

    for name, fn in (("corpus", lambda: push_corpus(data_root)), ("snapshot", push_snapshot)):
        try:
            results[name] = fn()
        except Exception as exc:
            logger.exception("offsite: %s target failed", name)
            results[name] = {"error": str(exc)}
            errors.append(f"{name}: {exc}")

    if errors:
        raise RuntimeError("offsite push failed — " + "; ".join(errors))
    return results
