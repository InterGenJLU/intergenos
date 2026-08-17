#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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

IGOS="${IGOS:-/mnt/igos}"

# Source the forensic-trace bash companion (no-op when verbose unset).
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_init "host-chroot-setup"
    trace_event chroot_setup_start target="$IGOS"
    _setup_trace_exit() {
        local rc=$?
        trace_event chroot_setup_end target="$IGOS" rc::=$rc
        trace_close
        return $rc
    }
    trap _setup_trace_exit EXIT
fi

echo "InterGenOS Chroot Setup"
echo "======================="
echo "Target: $IGOS"
echo ""

# Verify we're root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root."
    exit 1
fi

# Check system state and inform the user
if [ ! -d "$IGOS/usr/bin" ]; then
    echo "ERROR: $IGOS/usr/bin not found. The target system doesn't appear to be built."
    echo "       Build the toolchain (Chapters 5-6) before running this script."
    exit 1
fi

if [ -d "$IGOS/tools" ]; then
    echo "NOTE: /tools directory exists — the cross-toolchain is still present."
    echo "      This is expected if you haven't completed Chapter 7 cleanup yet."
else
    echo "NOTE: /tools directory is gone — Chapter 7 cleanup has been completed."
    echo "      This is the expected state for Chapter 8 builds."
fi

# --- 7.2: Changing Ownership (one-time, sentinel-gated) ---
# LFS 7.2: toolchain (Ch5-6) is built as an unprivileged user, so
# $IGOS/{usr,var,etc,tools,lib64} are build-user-owned; this converts them to
# root:root for Chapter 8. It is a ONE-TIME bootstrap step. The orchestrator
# re-runs chroot-setup.sh on every resume/--start-at that finds the chroot
# unmounted (build-intergenos.sh:1242); without a guard the recursive chown
# re-fires on an already-built chroot and flattens every legitimate
# service-user ownership (influxdb/etcd/caddy/valkey/lighttpd/fcron/systemd-*)
# to root -> squashfs collapses to 2 uids -> those daemons break on installed
# systems. Gate it behind a once-per-chroot sentinel (excluded from the squashfs
# in build-squashfs.sh, so it never ships); the idempotent 7.3 mounts below
# still run every invocation.
OWNERSHIP_SENTINEL="$IGOS/.igos-chroot-ownership-normalized"
echo "--- Changing ownership to root ---"
if [ -e "$OWNERSHIP_SENTINEL" ]; then
    echo "  SKIP: chroot already ownership-normalized (sentinel present);"
    echo "        re-running the recursive chown would flatten service-user ownership."
else

# Capture setuid + setgid binaries BEFORE chown — POSIX `chown` strips
# the setuid/setgid bits by default when ownership changes, so any
# setuid binary (sudo, su, mount, passwd, etc.) installed in /usr by
# package builds would lose its privileged-escalation bits after the
# chown below. Discovered in Build #9 dev1 live-VM verification:
# /usr/bin/sudo was 755 instead of 4755 → sudo refused to elevate.
# The pre-capture / post-restore pattern is robust to any setuid binary
# we may add to the package set in the future without code changes.
SETUID_CAPTURE=$(mktemp)
find $IGOS/usr $IGOS/var $IGOS/etc $IGOS/tools \
     \( -type f -a \( -perm -4000 -o -perm -2000 \) \) \
     -printf '%m\t%p\n' 2>/dev/null > "$SETUID_CAPTURE" || true
case $(uname -m) in
    x86_64)
        find $IGOS/lib64 \
             \( -type f -a \( -perm -4000 -o -perm -2000 \) \) \
             -printf '%m\t%p\n' 2>/dev/null >> "$SETUID_CAPTURE" || true
        ;;
esac
SETUID_COUNT=$(wc -l < "$SETUID_CAPTURE")
echo "  Captured $SETUID_COUNT setuid/setgid binary(s) for post-chown restore"

chown -R root:root $IGOS/{usr,var,etc,tools} 2>/dev/null || true
case $(uname -m) in
    x86_64) chown -R root:root $IGOS/lib64 2>/dev/null || true ;;
esac

# Restore setuid/setgid bits stripped by the chown above. Skip silently
# if the file no longer exists (a defensive guard — shouldn't happen
# during chroot-setup but cheap to harden).
RESTORED=0
while IFS=$'\t' read -r mode path; do
    if [ -n "$mode" ] && [ -n "$path" ] && [ -e "$path" ]; then
        chmod "$mode" "$path"
        RESTORED=$((RESTORED + 1))
    fi
done < "$SETUID_CAPTURE"
rm -f "$SETUID_CAPTURE"
if [ "$RESTORED" -gt 0 ]; then
    echo "  Restored setuid/setgid bits on $RESTORED binary(s) post-chown"
fi

# Mark this chroot ownership-normalized so future resumes / --start-at runs
# skip the destructive recursive chown above (one-time LFS 7.2 step).
: > "$OWNERSHIP_SENTINEL"
echo "  Ownership normalized; wrote sentinel $OWNERSHIP_SENTINEL"
fi

# Fix root directory ownership — the setup phase creates /mnt/igos owned by
# the build user (needed for unprivileged toolchain). From chroot-prep onward
# everything runs as root, so fix it now. Without this, systemd-tmpfiles
# refuses to create /tmp/.X11-unix (unsafe path transition) causing GDM auth loop.
chown root:root $IGOS
echo "  Done (including chroot root directory)"

# --- 7.3: Preparing Virtual Kernel File Systems ---
echo "--- Creating virtual filesystem mount points ---"
mkdir -pv $IGOS/{dev,proc,sys,run}

# --- 7.3.1: Mounting and Populating /dev ---
echo "--- Mounting /dev (bind mount from host) ---"
if ! mountpoint -q $IGOS/dev; then
    mount -v --bind /dev $IGOS/dev
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_chroot_mount bind /dev "$IGOS/dev" ""
else
    echo "  Already mounted"
fi

# --- 7.3.2: Mounting Virtual Kernel File Systems ---
echo "--- Mounting /dev/pts ---"
if ! mountpoint -q $IGOS/dev/pts; then
    mount -vt devpts devpts -o gid=5,mode=0620 $IGOS/dev/pts
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_chroot_mount devpts devpts "$IGOS/dev/pts" "gid=5,mode=0620"
else
    echo "  Already mounted"
fi

echo "--- Mounting /proc ---"
if ! mountpoint -q $IGOS/proc; then
    mount -vt proc proc $IGOS/proc
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_chroot_mount proc proc "$IGOS/proc" ""
else
    echo "  Already mounted"
fi

echo "--- Mounting /sys ---"
if ! mountpoint -q $IGOS/sys; then
    mount -vt sysfs sysfs $IGOS/sys
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_chroot_mount sysfs sysfs "$IGOS/sys" ""
else
    echo "  Already mounted"
fi

echo "--- Mounting /run ---"
if ! mountpoint -q $IGOS/run; then
    mount -vt tmpfs tmpfs $IGOS/run
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_chroot_mount tmpfs tmpfs "$IGOS/run" ""
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

# --- Forensic-trace sink: make IGOS_TRACE_ROOT reachable INSIDE the chroot ---
# In-chroot build phases (chroot-tools, core, base, core-extra, ch8/ch10, and
# the python-builder tiers) emit byte-level JSONL via lib/trace.sh /
# igos_trace.py. The chroot is self-contained (sources/scripts are COPIED in,
# not bind-mounted), so an IGOS_TRACE_ROOT that lives OUTSIDE the chroot tree
# (e.g. /mnt/jarvis-storage/...) is invisible to in-chroot writers and their
# JSONL is silently lost. A forensic sink is an I/O channel — same category as
# /dev, /proc, /sys, /run, which we already bind-mount — NOT build content, so
# binding it in does not violate the self-contained-sources principle. This is
# the ONLY way the orchestrator's run trail and the in-chroot trail land in the
# same durable file set under one runid. Unmounted in chroot-teardown.sh.
if [ -n "${IGOS_TRACE_ROOT:-}" ] \
    && { [[ "${IGOS_BUILD_DEBUG_VERBOSE:-}" =~ ^(1|true|yes|on)$ ]] \
         || [[ "${FORGE_DEBUG_VERBOSE:-}" =~ ^(1|true|yes|on)$ ]]; }; then
    echo "--- Binding forensic-trace sink into chroot (${IGOS_TRACE_ROOT}) ---"
    # The sink root must exist on the host side first.
    mkdir -p "${IGOS_TRACE_ROOT}"
    # Mountpoint inside the chroot, at the SAME absolute path so IGOS_TRACE_ROOT
    # resolves identically in and out of the chroot.
    mkdir -p "${IGOS}${IGOS_TRACE_ROOT}"
    if ! mountpoint -q "${IGOS}${IGOS_TRACE_ROOT}"; then
        mount -v --bind "${IGOS_TRACE_ROOT}" "${IGOS}${IGOS_TRACE_ROOT}"
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] \
            && trace_chroot_mount bind "${IGOS_TRACE_ROOT}" "${IGOS}${IGOS_TRACE_ROOT}" ""
    else
        echo "  Already mounted"
    fi
fi

# --- Timezone: match host ---
# The chroot has no zoneinfo database until glibc is built in Ch. 8.
# Without the actual zoneinfo files, TZ=America/Chicago resolves to UTC.
# Fix: copy the host's zoneinfo tree for the local timezone into the chroot
# so timestamps are correct from the very first chroot command.
#
# Symlink-leak defense (2026-05-24): rm -f the chroot's existing target
# BEFORE every cp into the chroot. glibc-core/build.sh creates
# /etc/localtime as an ABSOLUTE symlink (ln -sfv /usr/share/zoneinfo/$tz
# /etc/localtime). When this script runs on the VM (not inside the
# chroot), cp's default-follow-dest-symlink behavior resolves that
# absolute path against the VM's filesystem root, leaking the write
# out of the chroot and into the VM's /usr/share/zoneinfo/. Repeated
# save_checkpoint cycles then propagate the leaked corruption back
# into the chroot via the UTC/posixrules loop below. The rm -f's
# remove any existing symlink so cp creates a fresh regular file in
# the chroot rather than following a leaked-out absolute target.
echo "--- Syncing host timezone into chroot ---"
if [ -f /etc/localtime ]; then
    rm -f "$IGOS/etc/localtime"
    cp -fL /etc/localtime "$IGOS/etc/localtime"
    echo "  Copied host /etc/localtime"

    if [ -f /etc/timezone ]; then
        rm -f "$IGOS/etc/timezone"
        cp -f /etc/timezone "$IGOS/etc/timezone"
        HOST_TZ="$(cat /etc/timezone)"
        echo "  Copied host /etc/timezone ($HOST_TZ)"

        # Copy the specific zoneinfo file so TZ= resolves before glibc Ch.8
        HOST_ZONEINFO="/usr/share/zoneinfo/$HOST_TZ"
        if [ -f "$HOST_ZONEINFO" ]; then
            mkdir -p "$IGOS/usr/share/zoneinfo/$(dirname "$HOST_TZ")"
            rm -f "$IGOS/usr/share/zoneinfo/$HOST_TZ"
            cp -f "$HOST_ZONEINFO" "$IGOS/usr/share/zoneinfo/$HOST_TZ"
            echo "  Copied $HOST_ZONEINFO into chroot"
        fi

        # Also copy the UTC/posix fallbacks that date/printf may need
        for tz_file in UTC posixrules; do
            src="/usr/share/zoneinfo/$tz_file"
            if [ -f "$src" ]; then
                rm -f "$IGOS/usr/share/zoneinfo/$tz_file"
                cp -f "$src" "$IGOS/usr/share/zoneinfo/$tz_file"
            fi
        done
    fi
else
    echo "  WARNING: /etc/localtime not found on host, chroot will use UTC"
fi

echo ""
echo "======================="
echo "Chroot environment ready."
echo ""
echo "To enter:  sudo bash /mnt/intergenos/scripts/chroot-enter.sh"
echo "To build:  sudo bash /mnt/intergenos/scripts/chroot-enter.sh /mnt/intergenos/scripts/chroot-build.sh"
echo "To clean:  sudo bash /mnt/intergenos/scripts/chroot-teardown.sh"
