#!/usr/bin/env bash
# Deploys whatever is currently on origin/prod to the live SLEEPY stack on
# klm.smtw.in. Run from Git Bash, from the repo root:
#   tooling/deploy-prod.sh
#
# This script does NOT decide what goes into prod — that's the `main` ->
# `prod` fast-forward merge + push, done separately (see
# ".claude/CLAUDE.md#dev-prod-environment-separation--deploy-workflow").
# All this does is make the box match origin/prod: fetch, switch/pull,
# rebuild, restart, and verify. It refuses to run past any failed step
# (set -e end to end) and never force-pushes or force-resets anything.
#
# Safe to re-run — every step is idempotent (branch checkout, image prune,
# compose build/up are all no-ops or safe re-applies if already current).

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/aws-conn.sh"

echo "==> Deploying origin/prod to ${AWS_SSH_USER}@${AWS_SSH_HOST}:${REMOTE_DIR}"
echo

remote_cmd=$(cat <<EOF
set -e
cd ${REMOTE_DIR}

echo "--- fetch ---"
git fetch origin

echo "--- stash any local drift (never discarded outright — see git stash list) ---"
if ! git diff --quiet || ! git diff --cached --quiet; then
    git stash push -u -m "deploy-prod.sh auto-stash \$(date -Iseconds)"
    echo "  stashed local changes — inspect with 'git stash show -p' on the box if this matters"
else
    echo "  clean, nothing to stash"
fi

echo "--- switch to prod branch ---"
git checkout prod 2>/dev/null || git checkout -b prod origin/prod

echo "--- pull (fast-forward only) ---"
git pull --ff-only origin prod

echo "--- free disk before rebuild ---"
docker image prune -af

echo "--- build ---"
docker compose build

echo "--- restart ---"
docker compose up -d

echo "--- container status ---"
docker compose ps

echo "--- health check ---"
ok=0
for i in \$(seq 1 10); do
    if curl -sf http://localhost:5000/healthz >/dev/null; then
        ok=1
        break
    fi
    echo "  not ready yet, retrying (\$i/10)..."
    sleep 3
done
if [ "\$ok" != "1" ]; then
    echo "HEALTHCHECK FAILED after rebuild — service did not come up healthy." >&2
    exit 1
fi
echo "healthz OK"

echo
echo "--- deployed commit ---"
git log -1 --format='%h %s (%ci)'
EOF
)

aws_ssh_run "$remote_cmd"

echo
echo "==> Deploy finished successfully."
