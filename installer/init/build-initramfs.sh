#!/usr/bin/env bash
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
MODULES_SRC="${MODULES_DIR:-/lib/modules/$KVER}"

[ -f "$INIT_SCRIPT" ] || { echo "ERROR: init script not found: $INIT_SCRIPT" >&2; exit 1; }
[ -x "$BUSYBOX" ] || { echo "ERROR: busybox-static not found: $BUSYBOX" >&2; exit 1; }
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

APPLETS="sh mount umount switch_root awk blkid sleep modprobe mkdir cp ln echo cat printf grep sed find"
for applet in $APPLETS; do
    ln -sf busybox "$WORK/bin/$applet"
done

# Mirror critical /sbin links for compatibility
mkdir -p "$WORK/sbin"
for s in switch_root blkid modprobe; do
    ln -sf "/bin/$s" "$WORK/sbin/$s"
done

# ---- Kernel modules — required for live boot -------------------------------
# Modules and their dependencies must be physically present in the cpio
# (initramfs has no module-loader fallback to disk).
REQUIRED_MODULES="squashfs overlay loop isofs vfat ext4"

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
