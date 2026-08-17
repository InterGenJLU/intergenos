#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# build-ukis-verity.sh — host-side UKI assembly with dm-verity cmdline injection.
#
# Runs from the HOST (build VM), not from the chroot. Invoked by
# phase_ukis_verity AFTER phase_squashfs has emitted the verity hashtree
# and the params file (ROOT_HASH, DATA_BLOCKS, etc).
#
# Why host-side: phase_image cleans /mnt/intergenos + /sources + /tmp/* from
# inside the chroot and tears down the chroot pseudo-fs mounts. By the time
# we know the verity root hash (post-squashfs), the chroot context is no
# longer set up for chroot-build-bootloader.sh to run. The host has all the
# necessary artifacts staged in $BOOTLOADER_DIR by chroot-build-bootloader.sh
# (initramfs.cpio.gz + vmlinuz + microcode cpios + os-release).
#
# Reads the verity-params file emitted by build-squashfs.sh and appends a
# `igos.verity.roothash=<HASH>` token to each of the three mode cmdlines
# (live, install-gui, install-tui) before passing them to build-uki.sh.
# All three modes share init.sh, which reads the root hash from /proc/cmdline
# and activates dm-verity at boot via veritysetup-static.
#
# Why all three modes get the same verity treatment: they share one initramfs
# (the cmdline differentiates dispatch in init.sh). They all need to mount
# the same squashfs. dm-verity per-block verification benefits boot time on
# all three.
#
# Usage:
#   BOOTLOADER_DIR=/mnt/intergenos/build/bootloader \
#   VERITY_PARAMS=/path/to/filesystem.squashfs.verity-params \
#   scripts/build-ukis-verity.sh
#
# Optional env:
#   UKIFY    — path to ukify (default: /usr/bin/ukify or first in PATH)
#   STUB     — systemd-stub path (default: chroot's stub, then host's)
#   CMDLINE_DIR — where the static cmdline files live (default: installer/init/)
#   IGOS    — chroot root (default: /mnt/igos; used as fallback STUB search path)

set -euo pipefail

# --------------------------------------------------------------------------
# Inputs + defaults
# --------------------------------------------------------------------------

BOOTLOADER_DIR="${BOOTLOADER_DIR:?missing BOOTLOADER_DIR env var (e.g. /mnt/intergenos/build/bootloader)}"
VERITY_PARAMS="${VERITY_PARAMS:?missing VERITY_PARAMS env var (squashfs verity-params file emitted by build-squashfs.sh)}"

IGOS="${IGOS:-/mnt/igos}"
CMDLINE_DIR="${CMDLINE_DIR:-/mnt/intergenos/installer/init}"

# Locate ukify. Host's systemd-ukify package puts it in /usr/bin or /usr/sbin.
# Fall back to chroot's binary if the build VM doesn't have ukify installed.
UKIFY="${UKIFY:-}"
if [ -z "$UKIFY" ]; then
    for candidate in /usr/bin/ukify /usr/sbin/ukify "${IGOS}/usr/bin/ukify"; do
        if [ -x "$candidate" ]; then
            UKIFY="$candidate"
            break
        fi
    done
fi
[ -x "$UKIFY" ] || {
    echo "ERROR: ukify not found. Install systemd-ukify on the build VM" >&2
    echo "       (apt install systemd-ukify) or set UKIFY env var." >&2
    exit 1
}

# When using the chroot's ukify (the usual case — UKIs are assembled from the
# HOST since the dm-verity reorder, but the build VM has no systemd-ukify), its
# `#!/usr/bin/env python3` shebang runs under the BUILD VM's python, which lacks
# ukify's pure-python dep `pefile`. Point PYTHONPATH at the chroot's
# site-packages so the host python imports the chroot's pefile (pure-python, so
# it loads across interpreter minor versions). Keeps the build self-contained —
# no host-VM pip/apt install required.
case "$UKIFY" in
    "${IGOS}"/*)
        for sp in "${IGOS}"/usr/lib/python3*/site-packages; do
            [ -d "$sp" ] && PYTHONPATH="${sp}${PYTHONPATH:+:$PYTHONPATH}"
        done
        export PYTHONPATH
        ;;
esac

# Locate systemd-stub. The chroot's stub is the one that was used previously
# in chroot-build-bootloader.sh; prefer it for byte-identical reproducibility
# of UKIs that don't change verity params. Fall back to host's stub.
STUB="${STUB:-}"
if [ -z "$STUB" ]; then
    for candidate in \
        "${IGOS}/usr/lib/systemd/boot/efi/linuxx64.efi.stub" \
        /usr/lib/systemd/boot/efi/linuxx64.efi.stub ; do
        if [ -f "$candidate" ]; then
            STUB="$candidate"
            break
        fi
    done
fi
[ -f "$STUB" ] || {
    echo "ERROR: systemd-stub not found. Install systemd (built with " >&2
    echo "       -D bootloader=enabled) or set STUB env var." >&2
    exit 1
}

# --------------------------------------------------------------------------
# Required staged artifacts (produced by chroot-build-bootloader.sh)
# --------------------------------------------------------------------------

VMLINUZ="${BOOTLOADER_DIR}/vmlinuz"
INITRAMFS="${BOOTLOADER_DIR}/initramfs.cpio.gz"
OS_RELEASE="${BOOTLOADER_DIR}/os-release"

[ -f "$VMLINUZ" ]    || { echo "ERROR: vmlinuz not staged at $VMLINUZ" >&2; exit 1; }
[ -f "$INITRAMFS" ]  || { echo "ERROR: initramfs not staged at $INITRAMFS" >&2; exit 1; }
[ -f "$OS_RELEASE" ] || { echo "ERROR: os-release not staged at $OS_RELEASE" >&2; exit 1; }
[ -f "$VERITY_PARAMS" ] || { echo "ERROR: verity params file not found: $VERITY_PARAMS" >&2; exit 1; }

# Microcode cpios — NOT fully optional. The UKI must carry early-load microcode
# for the running CPU vendor (Intel needs intel-ucode, AMD needs amd-ucode). A
# generic ISO boots on both, so the canonical build stages BOTH; the other
# vendor's cpio is the only "optional" one. Require at least one — fail closed
# rather than silently shipping a UKI with zero microcode early-load.
MICROCODE_LIST=""
[ -f "${BOOTLOADER_DIR}/intel-ucode.img" ] && MICROCODE_LIST="${MICROCODE_LIST} ${BOOTLOADER_DIR}/intel-ucode.img"
[ -f "${BOOTLOADER_DIR}/amd-ucode.img" ]   && MICROCODE_LIST="${MICROCODE_LIST} ${BOOTLOADER_DIR}/amd-ucode.img"
MICROCODE_LIST="${MICROCODE_LIST# }"
[ -n "$MICROCODE_LIST" ] || {
    echo "ERROR: no microcode cpio staged at ${BOOTLOADER_DIR} — need intel-ucode.img" >&2
    echo "       and/or amd-ucode.img (at least the running CPU vendor's is required)." >&2
    exit 1
}

# --------------------------------------------------------------------------
# Parse verity params
# --------------------------------------------------------------------------

# Source the forensic-trace bash companion (no-op when verbose unset).
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_init "host-build-ukis-verity"
    _UV_START_MS=$(date +%s%3N)
    trace_event ukis_verity_phase_start verity_params="$VERITY_PARAMS" bootloader_dir="$BOOTLOADER_DIR"
fi

# shellcheck source=/dev/null
source "$VERITY_PARAMS"

[ -n "${ROOT_HASH:-}" ]   || { echo "ERROR: ROOT_HASH missing from $VERITY_PARAMS" >&2; exit 1; }
[ -n "${DATA_BLOCKS:-}" ] || { echo "ERROR: DATA_BLOCKS missing from $VERITY_PARAMS" >&2; exit 1; }
[ -n "${HASH_ALGO:-}" ]   || { echo "ERROR: HASH_ALGO missing from $VERITY_PARAMS" >&2; exit 1; }

echo "================================================================"
echo "  Building verity-augmented UKIs (live + install-gui + install-tui)"
echo "  root hash: $ROOT_HASH"
echo "  data blocks: $DATA_BLOCKS (4 KiB each)"
echo "  hash algo: $HASH_ALGO"
echo "  ukify: $UKIFY"
echo "  stub:  $STUB"
echo "  out:   $BOOTLOADER_DIR"
echo "================================================================"

# --------------------------------------------------------------------------
# Per-mode UKI build
# --------------------------------------------------------------------------
#
# Cmdline structure: the static cmdline file content + a single appended token
# carrying the verity root hash. init.sh's veritysetup-open call reads the
# hashtree's superblock at runtime for everything else (salt, block sizes,
# data block count) — those are stable across the hashtree file alongside
# the squashfs on the ISO.
#
# Cmdline token: `igos.verity.roothash=<HASH>` (our own namespace; kernel
# ignores unknown tokens). init.sh parses it via awk on /proc/cmdline.

mkdir -p /tmp/igos-ukis-verity
trap 'rm -rf /tmp/igos-ukis-verity' EXIT

for mode in live install-gui install-tui; do
    static_cmdline="${CMDLINE_DIR}/cmdline.${mode}.txt"
    augmented_cmdline="/tmp/igos-ukis-verity/cmdline.${mode}.txt"
    uki_output="${BOOTLOADER_DIR}/igos-${mode}.efi"

    [ -f "$static_cmdline" ] || { echo "ERROR: static cmdline missing: $static_cmdline" >&2; exit 1; }

    # Strip trailing newline from the static file then append the verity
    # token. Keep one trailing newline at the end to match the existing
    # cmdline file convention (build-uki.sh + ukify both tolerate it).
    {
        tr -d '\n' < "$static_cmdline"
        printf ' igos.verity.roothash=%s\n' "$ROOT_HASH"
    } > "$augmented_cmdline"

    echo ""
    echo "  -> mode=${mode}"
    echo "     cmdline: $(cat "$augmented_cmdline")"
    echo "     output:  $uki_output"

    # Remove any prior UKI in case this is a re-run.
    rm -f "$uki_output"

    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        trace_event ukis_verity_mode_start mode="$mode" \
            cmdline="$augmented_cmdline" output="$uki_output" \
            root_hash="$ROOT_HASH"
    fi
    UKIFY="$UKIFY" \
    VMLINUZ="$VMLINUZ" \
    INITRAMFS="$INITRAMFS" \
    CMDLINE="$augmented_cmdline" \
    OS_RELEASE="$OS_RELEASE" \
    OUTPUT="$uki_output" \
    STUB="$STUB" \
    MICROCODE="$MICROCODE_LIST" \
        bash /mnt/intergenos/scripts/build-uki.sh
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        _MODE_SIZE=$(stat -c%s "$uki_output" 2>/dev/null || echo 0)
        _MODE_SHA=$(sha256sum "$uki_output" 2>/dev/null | awk '{print $1}')
        trace_event ukis_verity_mode_done mode="$mode" output="$uki_output" \
            size_bytes::=$_MODE_SIZE sha="$_MODE_SHA" rc::=0
    fi
done

echo ""
echo "================================================================"
echo "  UKI build COMPLETE (unsigned, verity-augmented cmdlines)"
echo "================================================================"
ls -la "$BOOTLOADER_DIR"/*.efi 2>/dev/null
echo ""
echo "SHA-256:"
sha256sum "${BOOTLOADER_DIR}/igos-live.efi" \
          "${BOOTLOADER_DIR}/igos-install-gui.efi" \
          "${BOOTLOADER_DIR}/igos-install-tui.efi" 2>/dev/null \
    | sed 's/^/  /'
echo ""
echo "Next: operator signing ceremony (signs grub + 3 UKIs in one session)."

if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_event ukis_verity_phase_end \
        rc::=0 duration_ms::=$(( $(date +%s%3N) - _UV_START_MS )) \
        bootloader_dir="$BOOTLOADER_DIR"
    trace_close
fi
