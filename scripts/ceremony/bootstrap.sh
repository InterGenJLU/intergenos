#!/bin/bash
# bootstrap.sh — install offline-debs bundle, then exec ceremony.py.
# Run this ONE command on Tails:
#     bash /media/amnesia/OFFLINEDEBS/scripts/bootstrap.sh
# It installs python3-pexpect (needed by ceremony.py) plus the rest of the bundle,
# then hands off to ceremony.py for the actual ceremony.

set -eu

DRIVE2=/media/amnesia/OFFLINEDEBS
SCRIPTS="$DRIVE2/scripts"
DEBS="$DRIVE2/debian13"

if [ ! -d "$DRIVE2" ]; then
    echo "ERROR: Drive #2 not mounted at $DRIVE2"
    exit 2
fi

if [ ! -f "$SCRIPTS/ceremony.py" ]; then
    echo "ERROR: ceremony.py missing at $SCRIPTS/ceremony.py"
    exit 2
fi

# One-time NOPASSWD sudo setup (asks for password ONCE, then no more prompts)
if ! sudo -n true 2>/dev/null; then
    echo "================================================================"
    echo "  Sudo setup — enter your admin password ONCE"
    echo "  Sets passwordless sudo for the rest of the ceremony."
    echo "  Scoped to this Tails session — wiped on reboot (amnesic)."
    echo "================================================================"
    sudo bash -c 'rm -f /etc/sudoers.d/always-ask-password 2>/dev/null
echo "amnesia ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/zzzz-ceremony-nopasswd
chmod 0440 /etc/sudoers.d/zzzz-ceremony-nopasswd
echo "Defaults:amnesia !authenticate" >> /etc/sudoers.d/zzzz-ceremony-nopasswd'
    sudo -n true 2>/dev/null || { echo "ERROR: sudo setup failed"; exit 1; }
fi

# Install offline-debs (idempotent — dpkg -i is safe to re-run)
echo "Installing offline-debs from $DEBS..."
sudo -n dpkg -i "$DEBS"/*.deb 2>&1 | tail -5

# Verify pexpect is now available
if ! python3 -c "import pexpect" 2>/dev/null; then
    echo "ERROR: pexpect still not importable after dpkg install. Bundle may be incomplete."
    exit 2
fi
echo "OK pexpect available"

# Hand off to ceremony.py
exec python3 "$SCRIPTS/ceremony.py"
