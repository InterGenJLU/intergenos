#!/bin/bash
# InterGenOS Chroot Setup — LFS 13.0 Sections 7.2-7.3
#
# Runs as ROOT on the HOST (build VM), NOT inside the chroot.
# Prepares the target system for chroot entry:
#   1. Changes ownership from build user to root
#   2. Creates virtual kernel filesystem mount points
#   3. Mounts /dev, /dev/pts, /proc, /sys, /run, /dev/shm
#
# Usage (as root on build VM):
#   sudo bash /mnt/intergenos/scripts/chroot-setup.sh
#
# After this, use chroot-enter.sh to enter the chroot.

set -e

IGOS=/mnt/igos

echo "InterGenOS Chroot Setup"
echo "======================="
echo "Target: $IGOS"
echo ""

# Verify we're root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root."
    exit 1
fi

# Verify the target exists and has a toolchain
if [ ! -d "$IGOS/tools" ]; then
    echo "ERROR: $IGOS/tools not found. Build the toolchain first."
    exit 1
fi

# --- 7.2: Changing Ownership ---
echo "--- Changing ownership to root ---"
chown --from christopher -R root:root $IGOS/{usr,var,etc,tools} 2>/dev/null || true
case $(uname -m) in
    x86_64) chown --from christopher -R root:root $IGOS/lib64 2>/dev/null || true ;;
esac
echo "  Done"

# --- 7.3: Preparing Virtual Kernel File Systems ---
echo "--- Creating virtual filesystem mount points ---"
mkdir -pv $IGOS/{dev,proc,sys,run}

# --- 7.3.1: Mounting and Populating /dev ---
echo "--- Mounting /dev (bind mount from host) ---"
if ! mountpoint -q $IGOS/dev; then
    mount -v --bind /dev $IGOS/dev
else
    echo "  Already mounted"
fi

# --- 7.3.2: Mounting Virtual Kernel File Systems ---
echo "--- Mounting /dev/pts ---"
if ! mountpoint -q $IGOS/dev/pts; then
    mount -vt devpts devpts -o gid=5,mode=0620 $IGOS/dev/pts
else
    echo "  Already mounted"
fi

echo "--- Mounting /proc ---"
if ! mountpoint -q $IGOS/proc; then
    mount -vt proc proc $IGOS/proc
else
    echo "  Already mounted"
fi

echo "--- Mounting /sys ---"
if ! mountpoint -q $IGOS/sys; then
    mount -vt sysfs sysfs $IGOS/sys
else
    echo "  Already mounted"
fi

echo "--- Mounting /run ---"
if ! mountpoint -q $IGOS/run; then
    mount -vt tmpfs tmpfs $IGOS/run
else
    echo "  Already mounted"
fi

# Handle /dev/shm — may be a symlink or mount point depending on host
echo "--- Setting up /dev/shm ---"
if [ -h $IGOS/dev/shm ]; then
    install -v -d -m 1777 $IGOS$(realpath /dev/shm)
else
    if ! mountpoint -q $IGOS/dev/shm; then
        mount -vt tmpfs -o nosuid,nodev tmpfs $IGOS/dev/shm
    else
        echo "  Already mounted"
    fi
fi

echo ""
echo "======================="
echo "Chroot environment ready."
echo ""
echo "To enter:  sudo bash /mnt/intergenos/scripts/chroot-enter.sh"
echo "To build:  sudo bash /mnt/intergenos/scripts/chroot-enter.sh /mnt/intergenos/scripts/chroot-build.sh"
echo "To clean:  sudo bash /mnt/intergenos/scripts/chroot-teardown.sh"
