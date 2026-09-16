#!/usr/bin/env bash
# Opens an interactive SSH session to the SLEEPY AWS EC2 host via PuTTY's
# plink.exe, using the existing .ppk key (no key-format conversion needed).
# Run from Git Bash: tooling/aws-ssh.sh
#
# For scripted (non-interactive) actions against the box, use
# tooling/deploy-prod.sh or tooling/prod-status.sh instead of piping
# commands through this one.

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/aws-conn.sh"

exec "$PLINK" -hostkey "$HOSTKEY" -i "$KEY" "${AWS_SSH_USER}@${AWS_SSH_HOST}"
