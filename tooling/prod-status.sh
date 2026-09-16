#!/usr/bin/env bash
# Read-only health check of the live SLEEPY stack on klm.smtw.in — safe to
# run any time, mutates nothing on the box. Run from Git Bash:
#   tooling/prod-status.sh
#
# Shows: which commit/branch is actually deployed, container status, and
# the /healthz result, so you can tell at a glance whether prod matches
# what's on the `prod` git branch before or after a deploy-prod.sh run.

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/aws-conn.sh"

remote_cmd=$(cat <<EOF
set -e
cd ${REMOTE_DIR}
echo "=== git ==="
echo "branch: \$(git branch --show-current)"
echo "commit: \$(git log -1 --format='%h %s (%ci)')"
echo
echo "=== containers ==="
docker compose ps
echo
echo "=== disk ==="
df -h / | tail -n +1
echo
echo "=== healthz ==="
curl -sf http://localhost:5000/healthz && echo " -> OK" || echo " -> FAILED"
EOF
)

aws_ssh_run "$remote_cmd"
