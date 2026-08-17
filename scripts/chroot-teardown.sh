#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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

# Source the forensic-trace bash companion (no-op when verbose unset).
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_init "host-chroot-teardown"
    trace_event chroot_teardown_start target="$IGOS"
    _td_trace_exit() {
        local rc=$?
        trace_event chroot_teardown_end target="$IGOS" rc::=$rc
        trace_close
        return $rc
    }
    trap _td_trace_exit EXIT
fi

echo "InterGenOS Chroot Teardown"
echo "=========================="

# Unmount in reverse order of mounting
# Some may not be mounted — that's fine, we ignore errors

_trace_umount() {
    local target="$1"
    if umount "$target" 2>/dev/null; then
        echo "  Done"
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_chroot_unmount "$target"
    else
        echo "  Not mounted"
    fi
}

# Forensic-trace sink bind (chroot-setup.sh binds IGOS_TRACE_ROOT into the
# chroot). Unmount it FIRST — before /dev/shm and the API filesystems, and
# before any squashfs of the chroot tree — so the host sink is released cleanly
# and the bind mountpoint isn't captured as a live mount in the image.
if [ -n "${IGOS_TRACE_ROOT:-}" ] && mountpoint -q "${IGOS}${IGOS_TRACE_ROOT}" 2>/dev/null; then
    echo "--- Unmounting forensic-trace sink (${IGOS}${IGOS_TRACE_ROOT}) ---"
    _trace_umount "${IGOS}${IGOS_TRACE_ROOT}"
fi

echo "--- Unmounting /dev/shm ---"
_trace_umount "$IGOS/dev/shm"

echo "--- Unmounting /run ---"
_trace_umount "$IGOS/run"

echo "--- Unmounting /sys ---"
_trace_umount "$IGOS/sys"

echo "--- Unmounting /proc ---"
_trace_umount "$IGOS/proc"

echo "--- Unmounting /dev/pts ---"
_trace_umount "$IGOS/dev/pts"

echo "--- Unmounting /dev ---"
_trace_umount "$IGOS/dev"

echo ""
echo "=========================="
echo "Chroot environment torn down."
echo "To re-enter: run chroot-setup.sh first, then chroot-enter.sh"
