#!/usr/bin/env bash
# build-uki.sh — assemble a Unified Kernel Image (UKI) via systemd's ukify.
#
# UKI = single PE binary containing:
#   .linux       — vmlinuz
#   .initrd      — initramfs cpio.gz
#   .cmdline     — kernel cmdline
#   .osrel       — /etc/os-release
#   .uname       — kernel version string
#   .sbat        — SBAT revocation metadata (baked into stub at compile time)
#
# Wrapped by systemd-stub (the PE host). One signature on the resulting
# PE binary covers everything inside — the integrity property: kernel +
# initramfs fused under a single firmware-verifiable signature.
#
# IMPORTANT: this uses `ukify` (canonical systemd tool, ships with systemd >= 252).
# Previous implementation used raw `objcopy --add-section` which produced
# UKIs with incorrect `SizeOfImage` PE header field — strict UEFI loaders
# (e.g. Ubuntu's OVMF) reject those with EFI_LOAD_ERROR. ukify computes
# SizeOfImage correctly to cover all section VMAs. Migration 2026-05-14
# after Build #9's boot test surfaced the EFI_LOAD_ERROR.
#
# Q-INIT resolved 2026-05-05/06: April-10 custom-init stands. UKI wrapping
# happens here via systemd-stub (NOT dracut --uefi). ukify is the canonical
# wrapper around systemd-stub; same stub, correct PE assembly.
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
#   UKIFY        — ukify binary path (default: /usr/bin/ukify; override for testing)
#   MICROCODE    — optional space-separated list of microcode cpio paths
#                  (e.g. "/boot/intel-ucode.img /boot/amd-ucode.img"). Each
#                  is passed as an additional --initrd= ARGUMENT BEFORE
#                  $INITRAMFS so it loads first; ukify concatenates the
#                  --initrd inputs into one .initrd section in the order
#                  given (systemd ukify.py join_initrds()). Missing files
#                  are an error — caller must verify presence before passing.

set -euo pipefail

VMLINUZ="${VMLINUZ:?missing VMLINUZ env var}"
INITRAMFS="${INITRAMFS:?missing INITRAMFS env var}"
CMDLINE="${CMDLINE:?missing CMDLINE env var}"
OUTPUT="${OUTPUT:?missing OUTPUT env var}"

OS_RELEASE="${OS_RELEASE:-/etc/os-release}"
STUB="${STUB:-/usr/lib/systemd/boot/efi/linuxx64.efi.stub}"
UKIFY="${UKIFY:-/usr/bin/ukify}"
MICROCODE="${MICROCODE:-}"

[ -f "$VMLINUZ" ]    || { echo "ERROR: VMLINUZ not found: $VMLINUZ" >&2; exit 1; }
[ -f "$INITRAMFS" ]  || { echo "ERROR: INITRAMFS not found: $INITRAMFS" >&2; exit 1; }
[ -f "$CMDLINE" ]    || { echo "ERROR: CMDLINE not found: $CMDLINE" >&2; exit 1; }
[ -f "$OS_RELEASE" ] || { echo "ERROR: OS_RELEASE not found: $OS_RELEASE" >&2; exit 1; }
[ -f "$STUB" ]       || { echo "ERROR: systemd-stub not found: $STUB" >&2; \
                          echo "Install systemd (built with -D bootloader=enabled) or override STUB env var." >&2; \
                          exit 1; }
[ -x "$UKIFY" ]      || { echo "ERROR: ukify not found at $UKIFY" >&2; \
                          echo "Install systemd-ukify (Debian) or systemd (Arch, includes ukify) or override UKIFY env var." >&2; \
                          exit 1; }

# Build --initrd= argument list. Microcode cpios load FIRST (kernel reads
# the .initrd section sequentially; microcode must be applied before kernel
# init touches CPU features). $INITRAMFS is the main initramfs and goes last.
INITRD_ARGS=()
for ucode in $MICROCODE; do
    [ -f "$ucode" ] || { echo "ERROR: microcode cpio not found: $ucode" >&2; exit 1; }
    INITRD_ARGS+=( --initrd="$ucode" )
done
INITRD_ARGS+=( --initrd="$INITRAMFS" )

# Extract kernel uname (passed to ukify --uname; embedded in .uname section)
KVER=$(file "$VMLINUZ" | grep -oP 'version \K[^ ]+' | head -1)
[ -z "$KVER" ] && KVER="unknown"

echo "Building UKI via ukify:"
echo "  VMLINUZ:   $VMLINUZ"
echo "  INITRAMFS: $INITRAMFS"
echo "  MICROCODE: ${MICROCODE:-<none>}"
echo "  CMDLINE:   $CMDLINE"
echo "  OS_REL:    $OS_RELEASE"
echo "  STUB:      $STUB"
echo "  UKIFY:     $UKIFY ($("$UKIFY" --version 2>&1 | head -1))"
echo "  KVER:      $KVER"
echo "  OUTPUT:    $OUTPUT"

"$UKIFY" build \
    --linux="$VMLINUZ" \
    "${INITRD_ARGS[@]}" \
    --cmdline=@"$CMDLINE" \
    --os-release=@"$OS_RELEASE" \
    --uname="$KVER" \
    --stub="$STUB" \
    --output="$OUTPUT"

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
