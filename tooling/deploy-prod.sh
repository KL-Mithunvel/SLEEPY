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
# Image prune alone was never enough: it leaves the BUILD CACHE untouched, and
# that is what actually filled the disk — 7.2GB of it by 2026-09-20, none in
# use, while the prune above made it look like every deploy cleaned up after
# itself. Each deploy left a whole cache generation behind permanently.
#
# "until=24h" rather than -a on purpose. Deploys cluster in sessions (three in
# one afternoon is normal here), so keeping the last day's cache means those
# stay fast, while nothing survives long enough to accumulate across sessions.
# Pruning it all would force a full rebuild every time, including the ~127MB
# GeoIP download in Dockerfile.backend.
docker builder prune -f --filter until=24h
df -h / | tail -1

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
echo "--- capture usage snapshot for Admin > Server ---"
# The backend runs without a Docker socket on purpose, so from inside its
# container it can see the disk TOTAL but not what is using it — and on this
# box the biggest consumer (images, build cache) is exactly the invisible
# part. Capturing it here, from the host, is what lets the Server tab name a
# culprit instead of just showing a percentage.
#
# Stored as whatever Docker printed ("4.55GB"); system_stats.parse_docker_size
# normalises it, so the parsing lives somewhere testable rather than in shell.
# Best-effort by design: this runs AFTER the health gate, and every branch
# ends in an echo rather than a non-zero status, so a snapshot that cannot be
# written never turns an otherwise healthy deploy into a failed one.
snapshot_dir="data/klm/db"
if [ -d "\$snapshot_dir" ]; then
    docker_json=\$(docker system df --format '{{json .}}' 2>/dev/null | paste -sd, -) || docker_json=""
    printf '{"captured_at":"%s","commit":"%s","disk_line":"%s","docker":[%s]}\n' \\
        "\$(date -Iseconds)" \\
        "\$(git log -1 --format='%h')" \\
        "\$(df -h / | tail -1 | tr -s ' ')" \\
        "\$docker_json" \\
        > "\$snapshot_dir/deploy-snapshot.json" 2>/dev/null \\
        && echo "  wrote \$snapshot_dir/deploy-snapshot.json" \\
        || echo "  could not write snapshot (non-fatal)"
else
    echo "  \$snapshot_dir not found, skipping (non-fatal)"
fi

echo
echo "--- deployed commit ---"
git log -1 --format='%h %s (%ci)'
EOF
)

aws_ssh_run "$remote_cmd"

echo
echo "==> Deploy finished successfully."
