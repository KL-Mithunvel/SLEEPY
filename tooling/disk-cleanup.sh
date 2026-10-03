#!/usr/bin/env bash
# Host-side disk safety net for the AWS box. Runs from /etc/cron.d (installed
# by deploy-prod.sh) and does nothing unless the root volume is past the
# threshold, so on a healthy day it is a silent no-op.
#
#   tooling/disk-cleanup.sh [threshold_percent]     (default 80)
#
# Why this exists as a HOST script: the backend/worker run without a Docker
# socket by design, so nothing inside the stack can prune images or build
# cache. Deploys already prune, but a deploy only cleans up *before* its
# build, so each one leaves the previous generation behind until the next.
#
# Only removes things that can be rebuilt: images no container uses, build
# cache, old journal files. Never touches volumes (pma_data/chroma are live).
# Skips if a build is running so it cannot yank cache from under it.

set -uo pipefail

threshold="${1:-80}"
used=$(df --output=pcent / | tail -1 | tr -dc '0-9')

if [ "$used" -lt "$threshold" ]; then
    exit 0
fi

if pgrep -f 'docker( compose)? build|buildx' >/dev/null 2>&1; then
    logger -t sleepy-disk-cleanup "disk ${used}% but a build is running, skipping"
    exit 0
fi

logger -t sleepy-disk-cleanup "disk ${used}% >= ${threshold}%, pruning"
docker image prune -af >/dev/null 2>&1
docker builder prune -af >/dev/null 2>&1
sudo -n journalctl --vacuum-size=100M >/dev/null 2>&1
after=$(df --output=pcent / | tail -1 | tr -dc '0-9')
logger -t sleepy-disk-cleanup "disk now ${after}% (was ${used}%)"
