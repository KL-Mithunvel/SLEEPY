#!/usr/bin/env bash
# Shared connection constants for every AWS EC2 tooling script (aws-ssh.sh,
# deploy-prod.sh, prod-status.sh). Source it, don't execute it directly:
#   source "$(dirname "${BASH_SOURCE[0]}")/aws-conn.sh"
#
# One place to update if the .ppk key path, host, or host-key fingerprint
# ever changes (e.g. the instance is replaced) instead of three.

PLINK="/c/Program Files/PuTTY/plink.exe"
KEY="/c/Users/MithunvelKL/Documents/KLM-Web-Key.ppk"
AWS_SSH_USER="ec2-user"
AWS_SSH_HOST="klm.smtw.in"
# ssh-ed25519 host key fingerprint, pinned so plink never blocks on an
# interactive "is this the right server?" prompt.
HOSTKEY="SHA256:XZ7jE/nhJAJ0eCbvkJwvC3Cp4YlpX2sDf86WYpogrRs"
# Where the app repo lives on the box — not /opt/sleepy as docs/SETUP.md's
# generic instructions say, that path was never actually used on this
# instance (confirmed 2026-09-16).
REMOTE_DIR="${REMOTE_DIR:-sleepy}"

if [ ! -f "$PLINK" ]; then
    echo "plink.exe not found at: $PLINK" >&2
    exit 1
fi

if [ ! -f "$KEY" ]; then
    echo ".ppk key not found at: $KEY" >&2
    exit 1
fi

# Run a single non-interactive command on the box: aws_ssh_run "cmd string"
aws_ssh_run() {
    "$PLINK" -batch -hostkey "$HOSTKEY" -i "$KEY" "${AWS_SSH_USER}@${AWS_SSH_HOST}" "$1"
}
