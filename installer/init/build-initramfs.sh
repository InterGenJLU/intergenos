#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# build-initramfs.sh — assemble the InterGenOS live initramfs cpio archive.
#
# Output: gzip-compressed cpio (newc format) — the standard Linux initramfs
# format. Consumed by build-uki.sh which fuses it with vmlinuz + cmdline +
# os-release into a signed UKI.
#
# Q-INIT resolved 2026-05-05/06: April-10 custom-init stands. This script
# builds the cpio that the UKI's .initrd section will contain.
#
# Inputs (as positional args or env vars):
#   $1: kernel version (e.g., 6.18.10-igos)
#   $2 (optional): output path; defaults to /tmp/igos-initramfs-<KVER>.cpio.gz
#
# Required env-overridable inputs:
#   INIT_SCRIPT   — path to the custom /init (default: installer/init/init.sh)
#   BUSYBOX       — path to statically-linked busybox binary
#                   (default: /usr/bin/busybox.static, from busybox-static package)
#   MODULES_DIR   — kernel modules directory (default: /lib/modules/$KVER)

set -euo pipefail

KVER="${1:?usage: build-initramfs.sh <KVER> [<output-cpio.gz>]}"
OUTPUT="${2:-/tmp/igos-initramfs-${KVER}.cpio.gz}"

INIT_SCRIPT="${INIT_SCRIPT:-/mnt/intergenos/installer/init/init.sh}"
BUSYBOX="${BUSYBOX:-/usr/bin/busybox.static}"
# VERITYSETUP_STATIC — statically-linked veritysetup for dm-verity open in
# init.sh. Replaces the prior whole-file sha256 verification with kernel-
# level per-block verification at read time (lever 4, 2026-05-28). Same
# package as cryptsetup-static (packages/core/cryptsetup-static now emits
# both binaries side-by-side); FDE installs already had cryptsetup-static
# baked into the FDE initramfs, so the host has both in place.
VERITYSETUP_STATIC="${VERITYSETUP_STATIC:-/usr/lib/intergen/veritysetup-static}"
MODULES_SRC="${MODULES_DIR:-/lib/modules/$KVER}"

[ -f "$INIT_SCRIPT" ] || { echo "ERROR: init script not found: $INIT_SCRIPT" >&2; exit 1; }
[ -x "$BUSYBOX" ] || { echo "ERROR: busybox-static not found: $BUSYBOX" >&2; exit 1; }
[ -x "$VERITYSETUP_STATIC" ] || {
    echo "ERROR: veritysetup-static not found: $VERITYSETUP_STATIC" >&2
    echo "       packages/core/cryptsetup-static must be installed in the" >&2
    echo "       chroot before this script can run (it ships veritysetup-static" >&2
    echo "       alongside cryptsetup-static)." >&2
    exit 1
}
[ -d "$MODULES_SRC" ] || { echo "ERROR: kernel modules not found: $MODULES_SRC" >&2; exit 1; }

WORK=$(mktemp -d -t igos-initramfs-XXXXXX)
trap 'rm -rf "$WORK"' EXIT

# ---- Initramfs root layout -------------------------------------------------
mkdir -p "$WORK"/{bin,sbin,etc,proc,sys,dev,run,newroot,lib/modules,usr/lib}

# /init — the custom dispatcher
cp "$INIT_SCRIPT" "$WORK/init"
chmod +x "$WORK/init"

# Busybox + applet symlinks. Limited applet set: only what /init exec's.
cp "$BUSYBOX" "$WORK/bin/busybox"
chmod +x "$WORK/bin/busybox"

APPLETS="sh mount umount switch_root awk blkid sleep modprobe mkdir cp ln echo cat printf grep sed find sha256sum"
for applet in $APPLETS; do
    ln -sf busybox "$WORK/bin/$applet"
done

# Mirror critical /sbin links for compatibility
mkdir -p "$WORK/sbin"
for s in switch_root blkid modprobe; do
    ln -sf "/bin/$s" "$WORK/sbin/$s"
done

# veritysetup-static — for dm-verity activation of the squashfs at boot.
# Same shape as build-fde-initramfs.sh's cryptsetup-static placement: ship
# the binary at /sbin (LUKS/verity convention), symlink at /bin so any
# PATH lookup hits it regardless of which dir comes first.
cp "$VERITYSETUP_STATIC" "$WORK/sbin/veritysetup"
chmod +x "$WORK/sbin/veritysetup"
ln -sf "/sbin/veritysetup" "$WORK/bin/veritysetup"

# ---- Kernel modules — required for live boot -------------------------------
# Modules and their dependencies must be physically present in the cpio
# (initramfs has no module-loader fallback to disk).
# dm_verity + dm_mod listed explicitly as a defense-in-depth fallback.
# CONFIG_DM_VERITY=y lives in config/kernel/fragments/00-universal-baseline.config,
# but its BLK_DEV_DM dependency is only forced =y by
# config/kernel/fragments/99-intergenos-overrides.config — the baseline ships
# CONFIG_BLK_DEV_DM=m, which would otherwise downgrade DM_VERITY to =m under
# `make olddefconfig` (Audit D-1, 2026-05-29). Both symbols are HARD-asserted
# post-merge in packages/core/linux-kernel/build.sh, so the built-in path is the
# expectation; the BFS walk's `(builtin)` check no-ops cleanly here, and listing
# them keeps the live boot resilient if a future kernel-config change flips
# either symbol back to =m.
# af_alg + algif_hash: the AF_ALG kernel-crypto userspace interface that the
# static veritysetup (--with-crypto_backend=kernel) opens at init to compute the
# dm-verity hash. The baseline ships these =m (config/kernel/fragments/
# 00-universal-baseline.config:331-334); 99-intergenos-overrides.config forces
# them =y so they're built-in, but — exactly like dm_verity/dm_mod above — we
# also bundle + modprobe them (init.sh) as defense-in-depth so the live boot
# stays resilient if a future kernel-config change flips either back to =m.
# Without this the verity open fails at "Cannot initialize crypto backend".
REQUIRED_MODULES="squashfs overlay loop isofs vfat ext4 dm_verity dm_mod af_alg algif_hash"

MOD_DEST="$WORK/lib/modules/$KVER"
mkdir -p "$MOD_DEST"

# Copy each required module + walk its dependency closure
for mod in $REQUIRED_MODULES; do
    modpath=$(modinfo -k "$KVER" -F filename "$mod" 2>/dev/null || true)
    if [ -z "$modpath" ] || [ ! -f "$modpath" ]; then
        echo "WARNING: module '$mod' not found (built-in kernel?)" >&2
        continue
    fi
    rel=${modpath#"$MODULES_SRC/"}
    mkdir -p "$MOD_DEST/$(dirname "$rel")"
    cp -p "$modpath" "$MOD_DEST/$rel"

    # Resolve and copy dependencies
    deps=$(modinfo -k "$KVER" -F depends "$mod" 2>/dev/null | tr ',' ' ' || true)
    for dep in $deps; do
        [ -z "$dep" ] && continue
        deppath=$(modinfo -k "$KVER" -F filename "$dep" 2>/dev/null || true)
        if [ -n "$deppath" ] && [ -f "$deppath" ]; then
            depRel=${deppath#"$MODULES_SRC/"}
            mkdir -p "$MOD_DEST/$(dirname "$depRel")"
            cp -p "$deppath" "$MOD_DEST/$depRel" 2>/dev/null || true
        fi
    done
done

# Module dependency map (so modprobe inside initramfs can resolve)
depmod -b "$WORK" -a "$KVER" 2>&1 | grep -v "^$" || true

# ---- Build the cpio archive ------------------------------------------------
cd "$WORK"
find . -print0 \
    | cpio --null --create --format=newc 2>/dev/null \
    | gzip -9 > "$OUTPUT"

cd - > /dev/null

echo "Built initramfs: $OUTPUT"
echo "  Size:    $(stat -c%s "$OUTPUT" | numfmt --to=iec)"
echo "  SHA-256: $(sha256sum "$OUTPUT" | awk '{print $1}')"
echo ""
echo "Next: scripts/build-uki.sh wraps this initramfs into a UKI alongside vmlinuz."
