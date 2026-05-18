#!/usr/bin/env bash
# build-fde-initramfs.sh — assemble the InterGenOS FDE (LUKS-on-root) initramfs.
#
# Sibling to build-initramfs.sh (live ISO initramfs). Produces the gzip-
# compressed cpio (newc format) that the linux-kernel post-install hook
# bundles into the UKI's .initrd section on installed systems where Forge
# wired LUKS at install per D-001 (LUKS-at-install v1.0 ratified opt-in)
# + D-005 Phase D activation (UKI parity Option A, signed by user MOK).
#
# Runs in two contexts:
#   - Build-time: scripts/chroot-build-bootloader.sh invokes inside the
#     chroot during phase_bootloader, staging output at
#     /usr/lib/intergen/fde-initramfs.cpio.gz inside the chroot root so
#     the squashfs ships a known-good cpio for the original kernel.
#   - Runtime: packages/core/linux-kernel/hooks/post-install.sh invokes
#     after pkm install/upgrade of linux-kernel, regenerating the cpio
#     against the new kernel's modules (covers pkm-upgrade KVER changes
#     where the build-time cpio's modules would be stale).
#
# Plain (non-LUKS) installs do not need this script. Per 2026-04-09
# ratification narrowed by D-001/D-005, plain installs boot with
# kernel-builtin storage drivers + PARTUUID + rootwait; the UKI's
# bundled cpio for those is intel-ucode.img only.
#
# Inputs (positional or env):
#   $1: kernel version (e.g., 6.18.10-igos)
#   $2 (optional): output path; defaults to /usr/lib/intergen/fde-initramfs.cpio.gz
#
# Required env-overridable inputs:
#   INIT_SCRIPT       — path to fde-init.sh (default: sibling fde-init.sh)
#   BUSYBOX           — path to statically-linked busybox
#                       (default: /usr/bin/busybox.static, from busybox-static package)
#   CRYPTSETUP_STATIC — path to statically-linked cryptsetup binary
#                       (default: /usr/lib/intergen/cryptsetup-static, from cryptsetup-static package)
#   MODULES_DIR       — kernel modules directory (default: /lib/modules/$KVER)

set -euo pipefail

KVER="${1:?usage: build-fde-initramfs.sh <KVER> [<output-cpio.gz>]}"
OUTPUT="${2:-/usr/lib/intergen/fde-initramfs.cpio.gz}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INIT_SCRIPT="${INIT_SCRIPT:-$SCRIPT_DIR/fde-init.sh}"
BUSYBOX="${BUSYBOX:-/usr/bin/busybox.static}"
CRYPTSETUP_STATIC="${CRYPTSETUP_STATIC:-/usr/lib/intergen/cryptsetup-static}"
MODULES_SRC="${MODULES_DIR:-/lib/modules/$KVER}"

[ -f "$INIT_SCRIPT" ] || { echo "ERROR: FDE init script not found: $INIT_SCRIPT" >&2; exit 1; }
[ -x "$BUSYBOX" ]    || { echo "ERROR: busybox-static not found: $BUSYBOX" >&2; exit 1; }
[ -x "$CRYPTSETUP_STATIC" ] || {
    echo "ERROR: cryptsetup-static not found: $CRYPTSETUP_STATIC" >&2
    echo "       The build-system coordinator's cryptsetup-static package" >&2
    echo "       must be installed in the chroot before this script can run." >&2
    exit 1
}
[ -d "$MODULES_SRC" ] || { echo "ERROR: kernel modules not found: $MODULES_SRC" >&2; exit 1; }

WORK=$(mktemp -d -t igos-fde-initramfs-XXXXXX)
trap 'rm -rf "$WORK"' EXIT

# ---- Initramfs root layout -------------------------------------------------
mkdir -p "$WORK"/{bin,sbin,etc,proc,sys,dev,run,newroot,lib/modules,usr/lib}

# /init — the FDE unlock dispatcher
cp "$INIT_SCRIPT" "$WORK/init"
chmod +x "$WORK/init"

# Busybox + applet symlinks. fde-init.sh's call surface: sh / mount / umount
# / switch_root / awk / blkid / sleep / modprobe / mkdir / cp / ln / echo
# / cat / printf / grep / sed / find / sha256sum (the live-init applet set,
# minus none — kept aligned for consistency with build-initramfs.sh).
cp "$BUSYBOX" "$WORK/bin/busybox"
chmod +x "$WORK/bin/busybox"

APPLETS="sh mount umount switch_root awk blkid sleep modprobe mkdir cp ln echo cat printf grep sed find sha256sum"
for applet in $APPLETS; do
    ln -sf busybox "$WORK/bin/$applet"
done

# Mirror critical /sbin links for compatibility (build-initramfs.sh parity)
mkdir -p "$WORK/sbin"
for s in switch_root blkid modprobe; do
    ln -sf "/bin/$s" "$WORK/sbin/$s"
done

# cryptsetup binary — statically linked, ships at /sbin per LUKS convention.
# fde-init.sh invokes bare `cryptsetup`; PATH lookup hits /bin first then
# /sbin. Symlink /bin/cryptsetup -> /sbin/cryptsetup for belt-and-suspenders
# coverage of busybox shells that don't put /sbin on PATH.
cp "$CRYPTSETUP_STATIC" "$WORK/sbin/cryptsetup"
chmod +x "$WORK/sbin/cryptsetup"
ln -sf "/sbin/cryptsetup" "$WORK/bin/cryptsetup"

# ---- Kernel modules — required for LUKS unlock + ext4 root mount ----------
# Modules and their transitive dependencies must be physically present in
# the cpio (initramfs has no module-loader fallback to disk).
#
# REQUIRED set:
#   dm_crypt     — LUKS unlock via device-mapper (the load-bearing module)
#   dm_mod       — device-mapper core (dm_crypt depends on it; listed
#                  explicitly so build-time hardening catches absence
#                  even if dep resolution is gimped)
#   ext4         — root filesystem driver (post-unlock root is ext4)
#   sd_mod       — SCSI/SATA disks (laptops, real hardware)
#   virtio_blk   — virtio block (VM testing path)
#   virtio_pci   — virtio PCI bus (companion to virtio_blk)
#   ahci         — SATA controller most modern hardware uses
#   nvme         — NVMe SSDs (most modern laptops including IGOSC's HP)
#   usb_storage  — USB block-device LUKS targets (rare but supported)
REQUIRED_MODULES="dm_crypt dm_mod ext4 sd_mod virtio_blk virtio_pci ahci nvme usb_storage"

MOD_DEST="$WORK/lib/modules/$KVER"
mkdir -p "$MOD_DEST"

# Fixed-point dependency closure. Improvement on build-initramfs.sh's
# 1-level walk (audit row E-005): dm_crypt pulls in crypto modules
# transitively (crc32c, sha256, aes, xts via the kernel crypto API);
# 1-level resolution would miss them and the kernel would refuse to
# initialize dm-crypt at boot with "unknown symbol" errors. Use a
# breadth-first walk: enqueue REQUIRED_MODULES, pop, resolve direct deps,
# enqueue any unseen, repeat until queue empty. Modules already built-in
# (modinfo returns "(builtin)" or empty path) are skipped — kernel
# already has them; explicit copy would no-op.
declare -A SEEN
queue=()
for mod in $REQUIRED_MODULES; do
    queue+=("$mod")
done

while [ "${#queue[@]}" -gt 0 ]; do
    mod="${queue[0]}"
    queue=("${queue[@]:1}")
    [ -n "${SEEN[$mod]:-}" ] && continue
    SEEN[$mod]=1

    modpath=$(modinfo -k "$KVER" -F filename "$mod" 2>/dev/null || true)
    if [ -z "$modpath" ] || [ "$modpath" = "(builtin)" ] || [ ! -f "$modpath" ]; then
        # Built-in or absent — modprobe inside initramfs treats as noop.
        # Common for crypto primitives compiled =y in the universal-
        # baseline kernel config.
        continue
    fi

    rel=${modpath#"$MODULES_SRC/"}
    mkdir -p "$MOD_DEST/$(dirname "$rel")"
    cp -p "$modpath" "$MOD_DEST/$rel"

    # Enqueue dependencies (recursive via the BFS queue)
    deps=$(modinfo -k "$KVER" -F depends "$mod" 2>/dev/null | tr ',' ' ' || true)
    for dep in $deps; do
        [ -z "$dep" ] && continue
        [ -n "${SEEN[$dep]:-}" ] || queue+=("$dep")
    done
done

# Module dependency map (so modprobe inside the initramfs can resolve)
depmod -b "$WORK" -a "$KVER" 2>&1 | grep -v "^$" || true

# ---- Build the cpio archive ------------------------------------------------
mkdir -p "$(dirname "$OUTPUT")"
cd "$WORK"
find . -print0 \
    | cpio --null --create --format=newc 2>/dev/null \
    | gzip -9 > "$OUTPUT"

cd - > /dev/null

echo "Built FDE initramfs: $OUTPUT"
echo "  Size:    $(stat -c%s "$OUTPUT" | numfmt --to=iec)"
echo "  SHA-256: $(sha256sum "$OUTPUT" | awk '{print $1}')"
echo ""
echo "Next: packages/core/linux-kernel/hooks/post-install.sh detects /etc/crypttab + bundles this initramfs into the UKI via --initrd=$OUTPUT alongside intel-ucode.img."
