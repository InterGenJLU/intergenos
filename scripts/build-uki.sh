#!/usr/bin/env bash
# build-uki.sh — assemble a Unified Kernel Image (UKI) from constituent parts.
#
# UKI = single PE binary containing:
#   .linux       — vmlinuz
#   .initrd      — initramfs cpio.gz
#   .cmdline     — kernel cmdline
#   .osrel       — /etc/os-release
#   .uname       — kernel version string
#
# The PE wrapper is systemd-stub (provided by the systemd-boot-efi package
# on Debian-family, or systemd-udeb on RHEL-family). One signature on the
# resulting PE binary covers everything inside — that's the Holy-Grail
# integrity property: kernel + initramfs fused under a single firmware-
# verifiable signature.
#
# Q-INIT resolved 2026-05-05/06: April-10 custom-init stands. UKI wrapping
# happens here via systemd-stub + objcopy (NOT dracut --uefi).
#
# Usage:
#   VMLINUZ=/path/to/vmlinuz \
#   INITRAMFS=/path/to/initramfs.cpio.gz \
#   CMDLINE=/path/to/cmdline.txt \
#   OUTPUT=/path/to/igos-live.efi \
#   scripts/build-uki.sh
#
# Optional env vars:
#   STUB         — systemd-stub path (default: /usr/lib/systemd/boot/efi/linuxx64.efi.stub)
#   OS_RELEASE   — os-release file (default: /etc/os-release on chroot/installed system)

set -euo pipefail

VMLINUZ="${VMLINUZ:?missing VMLINUZ env var}"
INITRAMFS="${INITRAMFS:?missing INITRAMFS env var}"
CMDLINE="${CMDLINE:?missing CMDLINE env var}"
OUTPUT="${OUTPUT:?missing OUTPUT env var}"

OS_RELEASE="${OS_RELEASE:-/etc/os-release}"
STUB="${STUB:-/usr/lib/systemd/boot/efi/linuxx64.efi.stub}"

[ -f "$VMLINUZ" ]    || { echo "ERROR: VMLINUZ not found: $VMLINUZ" >&2; exit 1; }
[ -f "$INITRAMFS" ]  || { echo "ERROR: INITRAMFS not found: $INITRAMFS" >&2; exit 1; }
[ -f "$CMDLINE" ]    || { echo "ERROR: CMDLINE not found: $CMDLINE" >&2; exit 1; }
[ -f "$OS_RELEASE" ] || { echo "ERROR: OS_RELEASE not found: $OS_RELEASE" >&2; exit 1; }
[ -f "$STUB" ]       || { echo "ERROR: systemd-stub not found: $STUB" >&2; \
                          echo "Install systemd-boot-efi (Debian) or systemd-boot (Arch/RHEL) or override STUB env var." >&2; \
                          exit 1; }

# Extract kernel uname (used by stub for displaying version on boot screen)
KVER=$(file "$VMLINUZ" | grep -oP 'version \K[^ ]+' | head -1)
[ -z "$KVER" ] && KVER="unknown"

# Section addresses per systemd's mkosi/ukify convention
# These are well-known offsets the systemd-stub expects.
OFFSET_OSREL=0x20000
OFFSET_CMDLINE=0x30000
OFFSET_LINUX=0x2000000
OFFSET_INITRD=0x4000000
OFFSET_UNAME=0x40000

UNAME_TXT=$(mktemp -t uki-uname-XXXXXX)
echo -n "$KVER" > "$UNAME_TXT"
trap 'rm -f "$UNAME_TXT"' EXIT

echo "Building UKI:"
echo "  VMLINUZ:   $VMLINUZ"
echo "  INITRAMFS: $INITRAMFS"
echo "  CMDLINE:   $CMDLINE"
echo "  OS_REL:    $OS_RELEASE"
echo "  STUB:      $STUB"
echo "  KVER:      $KVER"
echo "  OUTPUT:    $OUTPUT"

objcopy \
    --add-section .osrel="$OS_RELEASE"   --change-section-vma .osrel=$OFFSET_OSREL \
    --add-section .cmdline="$CMDLINE"    --change-section-vma .cmdline=$OFFSET_CMDLINE \
    --add-section .uname="$UNAME_TXT"    --change-section-vma .uname=$OFFSET_UNAME \
    --add-section .linux="$VMLINUZ"      --change-section-vma .linux=$OFFSET_LINUX \
    --add-section .initrd="$INITRAMFS"   --change-section-vma .initrd=$OFFSET_INITRD \
    "$STUB" "$OUTPUT"

UKI_SIZE=$(stat -c%s "$OUTPUT")
UKI_SIZE_MB=$((UKI_SIZE / 1024 / 1024))

echo ""
echo "Built UKI: $OUTPUT"
echo "  Size:    ${UKI_SIZE_MB} MB ($UKI_SIZE bytes)"
echo "  SHA-256: $(sha256sum "$OUTPUT" | awk '{print $1}')"
echo ""

if [ "$UKI_SIZE_MB" -gt 200 ]; then
    echo "WARNING: UKI exceeds typical OVMF firmware-load limit of ~200 MB" >&2
    echo "Consider trimming initramfs (build-initramfs.sh) — fewer kernel modules." >&2
fi

echo "Sign with: scripts/sign-kernel-uki.sh $OUTPUT $OUTPUT.signed"
