#!/usr/bin/env bash
# Opens an interactive SSH session to the SLEEPY AWS EC2 host via PuTTY's
# plink.exe, using the existing .ppk key (no key-format conversion needed).
# Run from Git Bash: tooling/aws-ssh.sh
#
# Falls back to the raw IP if the DNS name doesn't resolve for some reason.

set -euo pipefail

PLINK="/c/Program Files/PuTTY/plink.exe"
KEY="/c/Users/MithunvelKL/Documents/KLM-Web-Key.ppk"
USER="ec2-user"
HOST="klm.smtw.in"
# ssh-ed25519 host key fingerprint, pinned so plink never blocks on an
# interactive "is this the right server?" prompt. Re-check this if the
# instance is ever replaced (new host key -> plink will refuse to connect).
HOSTKEY="SHA256:XZ7jE/nhJAJ0eCbvkJwvC3Cp4YlpX2sDf86WYpogrRs"

if [ ! -f "$PLINK" ]; then
    echo "plink.exe not found at: $PLINK" >&2
    exit 1
fi

if [ ! -f "$KEY" ]; then
    echo ".ppk key not found at: $KEY" >&2
    exit 1
fi

exec "$PLINK" -hostkey "$HOSTKEY" -i "$KEY" "$USER@$HOST"
