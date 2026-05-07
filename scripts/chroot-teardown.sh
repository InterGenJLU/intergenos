#!/bin/bash
set -euo pipefail
# InterGenOS Chroot Teardown — "Drop Out"
#
# Unmounts virtual kernel filesystems in the correct order.
# Safe to run even if some mounts aren't present.
#
# Usage (as root on build VM):
#   sudo bash /mnt/intergenos/scripts/chroot-teardown.sh
#   IGOS=/custom/root sudo bash /mnt/intergenos/scripts/chroot-teardown.sh

IGOS="${IGOS:-/mnt/igos}"

# Defensive validation — if $IGOS is empty or "/", unmounting
# would target host filesystems, which is catastrophic.
if [ -z "$IGOS" ] || [ "$IGOS" = "/" ]; then
    echo "ERROR: \$IGOS is empty or '/' — refusing to unmount host filesystems"
    exit 1
fi

if [ ! -d "$IGOS" ]; then
    echo "WARNING: $IGOS does not exist — nothing to unmount"
    exit 0
fi

# Verify we're root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root."
    exit 1
fi

echo "InterGenOS Chroot Teardown"
echo "=========================="

# Unmount in reverse order of mounting
# Some may not be mounted — that's fine, we ignore errors

echo "--- Unmounting /dev/shm ---"
umount $IGOS/dev/shm 2>/dev/null && echo "  Done" || echo "  Not mounted"

echo "--- Unmounting /run ---"
umount $IGOS/run 2>/dev/null && echo "  Done" || echo "  Not mounted"

echo "--- Unmounting /sys ---"
umount $IGOS/sys 2>/dev/null && echo "  Done" || echo "  Not mounted"

echo "--- Unmounting /proc ---"
umount $IGOS/proc 2>/dev/null && echo "  Done" || echo "  Not mounted"

echo "--- Unmounting /dev/pts ---"
umount $IGOS/dev/pts 2>/dev/null && echo "  Done" || echo "  Not mounted"

echo "--- Unmounting /dev ---"
umount $IGOS/dev 2>/dev/null && echo "  Done" || echo "  Not mounted"

echo ""
echo "=========================="
echo "Chroot environment torn down."
echo "To re-enter: run chroot-setup.sh first, then chroot-enter.sh"
