#!/bin/bash
# intel-ucode post-install hook — refresh the early-load microcode image and
# the UKI on the target system.
#
# Fires at RUNTIME after pkm deploys the intel-ucode package (Forge install
# and `pkm upgrade` alike). Before this hook existed, a standalone
# intel-ucode upgrade updated /lib/firmware/intel-ucode/ only: nothing
# regenerated /boot/intel-ucode.img and nothing rebuilt the UKI, so the new
# microcode never reached the boot path until the next kernel install.
# (Decided 2026-07-24.)
#
# The linux-kernel package's post-install hook already owns the full
# boot-chain rebuild (microcode cpio regeneration for both vendors, FDE
# initramfs, ukify, MOK signing, retention prune). The correct behavior
# here is to CHAIN to that hook, not to reimplement any part of it — one
# rebuild path, one place it can be wrong.
#
# pkm provides env: PKM_PACKAGE_NAME, PKM_PACKAGE_VERSION, PKM_PACKAGE_ROOT.
# The chained kernel hook logs the triggering package name from that env.
#
# Best-effort throughout: this hook NEVER fails the package install (exit 0
# on every path), matching the kernel hook's own degrade semantics.

set -uo pipefail

# Same persistent log file as the kernel hook — boot-chain operations keep
# one place to look during boot-time troubleshooting.
LOGFILE=/var/log/intergen-kernel-postinstall.log
mkdir -p "$(dirname "$LOGFILE")" 2>/dev/null || true

log() {
    local msg="[intel-ucode:post-install] $*"
    echo "$msg" >&2
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') $msg" >> "$LOGFILE" 2>/dev/null || true
}

KERNEL_HOOK=/var/lib/pkm/hooks/linux-kernel/post-install
MICROCODE_HELPER=/usr/lib/intergen/build-microcode-cpio.sh

if [ -x "$KERNEL_HOOK" ]; then
    log "chaining to $KERNEL_HOOK (microcode cpio regeneration + UKI rebuild for the installed kernel)"
    if "$KERNEL_HOOK"; then
        log "boot-chain refresh complete"
    else
        log "WARNING: chained kernel hook exited $? — the boot path may still carry the prior microcode image; manual re-run: sudo $KERNEL_HOOK"
    fi
    exit 0
fi

# Fallback: the kernel hook is not present. The expected case is
# first-install package ordering (intel-ucode extracts before linux-kernel;
# the kernel's own hook fires later in the same install and performs the
# full rebuild). Refresh the early-load image so a populated /boot carries
# current microcode; the UKI is NOT rebuilt on this path, and the log says so.
#
# Precondition check before attempting: the helper hard-fails when firmware
# is present but its tool is not (intel-ucode/ without iucode_tool, or
# amd-ucode/ without cpio) — under dependency-derived first-install order
# the firmware routinely lands before the tooling, so that failure shape is
# by-design sequencing, not an error. Skip with an info line instead of
# attempting and warning; the kernel hook regenerates everything later in
# the same install. A helper failure WITH preconditions met still warns.
helper_preconditions_met() {
    if [ -d /lib/firmware/intel-ucode ] \
        && ! [ -x /usr/sbin/iucode_tool ] \
        && ! command -v iucode_tool >/dev/null 2>&1; then
        return 1
    fi
    if [ -d /lib/firmware/amd-ucode ] && ! command -v cpio >/dev/null 2>&1; then
        return 1
    fi
    return 0
}

if [ -x "$MICROCODE_HELPER" ] && ! helper_preconditions_met; then
    log "$KERNEL_HOOK absent and microcode tooling not yet deployed (first-install ordering) — skipping; the kernel hook performs the full rebuild later in this install"
elif [ -x "$MICROCODE_HELPER" ]; then
    log "$KERNEL_HOOK absent — regenerating microcode cpios via $MICROCODE_HELPER (UKI not rebuilt on this path)"
    if OUTPUT_DIR=/boot "$MICROCODE_HELPER" >/dev/null 2>&1; then
        log "microcode cpios refreshed at /boot"
    else
        log "WARNING: $MICROCODE_HELPER failed (exit $?) — existing /boot microcode images left as-is"
    fi
elif command -v iucode_tool >/dev/null 2>&1; then
    # Same full-pack invocation as the build-time path: no --scan-system,
    # the kernel selects the matching signature at boot.
    log "$KERNEL_HOOK and $MICROCODE_HELPER absent — writing /boot/intel-ucode.img directly (UKI not rebuilt on this path)"
    rm -f /boot/intel-ucode.img
    if iucode_tool /lib/firmware/intel-ucode/ --write-earlyfw=/boot/intel-ucode.img; then
        log "/boot/intel-ucode.img written"
    else
        log "WARNING: iucode_tool failed (exit $?) — /boot/intel-ucode.img not regenerated"
    fi
else
    log "no rebuild tooling present (kernel hook, microcode helper, iucode_tool all absent) — firmware files deployed, boot image untouched"
fi

exit 0
