#!/usr/bin/env bash
# chroot-build-bootloader.sh — assemble unsigned bootloader artifacts inside chroot.
#
# Runs INSIDE the chroot (invoked by phase_bootloader via chroot-enter.sh).
#
# Produces three unsigned artifacts under /mnt/intergenos/build/bootloader/:
#   - grubx64.efi       (standalone GRUB EFI binary, unsigned)
#   - initramfs.cpio.gz (live initramfs with busybox-static + custom init + kernel modules)
#   - igos-live.efi     (Unified Kernel Image: vmlinuz + initramfs + cmdline + os-release, unsigned)
#
# These artifacts are signed OFFLINE via scripts/sign-release.sh in a separate
# operator workflow (NK#1 PIV slot 9c). The orchestrator does NOT invoke signing.
# Per owner-direct architectural decision: orchestrator stops at unsigned
# artifacts; offline ceremony is operator-paced.
#
# Recommended operator workflow:
#   1. Run build-intergenos.sh --stop-after bootloader  (produces unsigned artifacts here)
#   2. Copy artifacts to offline workstation
#   3. Run sign-release.sh on offline workstation (signs grubx64.efi + igos-live.efi)
#   4. Copy signed artifacts back into chroot at /boot/efi/EFI/igos/
#   5. Run build-intergenos.sh --start-at image  (packages chroot into bootable QCOW2)

set -euo pipefail

# ---- Resolve kernel version from the kernel package metadata --------------
KVER_FILE=/mnt/intergenos/packages/core/linux-kernel/package.yml
if [ ! -f "$KVER_FILE" ]; then
    echo "ERROR: kernel package metadata not found: $KVER_FILE" >&2
    echo "Hint: chroot may not have packages/ synced (sync_chroot_scripts in orchestrator)." >&2
    exit 1
fi
KVER=$(grep '^version:' "$KVER_FILE" | awk '{print $2}' | tr -d '"')
SUFFIX="-igos"  # matches CONFIG_LOCALVERSION in kernel config
FULL_KVER="${KVER}${SUFFIX}"

OUT_DIR=/mnt/intergenos/build/bootloader
mkdir -p "$OUT_DIR"

# ---- Sanity-check the prerequisites the sub-scripts will need -------------
fail() { echo "ERROR: $*" >&2; exit 1; }

[ -x /usr/bin/busybox.static ] || fail "busybox-static not installed in chroot. Build packages/core/busybox-static first."

VMLINUZ_PATH="/boot/vmlinuz-$FULL_KVER"
[ -f "$VMLINUZ_PATH" ] || fail "kernel image not found: $VMLINUZ_PATH (kernel phase did not install? check phase_kernel output)"

[ -d "/lib/modules/$FULL_KVER" ] || fail "kernel modules not found: /lib/modules/$FULL_KVER"

[ -f /mnt/intergenos/installer/init/init.sh ]      || fail "init script missing: /mnt/intergenos/installer/init/init.sh"
[ -f /mnt/intergenos/installer/init/cmdline.txt ]  || fail "cmdline missing: /mnt/intergenos/installer/init/cmdline.txt"
[ -f /mnt/intergenos/installer/init/build-initramfs.sh ] || fail "initramfs build script missing"

[ -x /mnt/intergenos/scripts/build-grub-standalone.sh ] || fail "build-grub-standalone.sh missing"
[ -x /mnt/intergenos/scripts/build-uki.sh ]             || fail "build-uki.sh missing"

# systemd-stub: build-uki.sh's default expects /usr/lib/systemd/boot/efi/linuxx64.efi.stub
STUB=/usr/lib/systemd/boot/efi/linuxx64.efi.stub
[ -f "$STUB" ] || fail "systemd-stub not found at $STUB (systemd build with -D bootloader=enabled? see commit a851371)"

if ! command -v grub-mkstandalone >/dev/null 2>&1; then
    fail "grub-mkstandalone not in PATH (grub package installed in chroot?)"
fi

echo "================================================================"
echo "  Bootloader phase: unsigned artifact assembly"
echo "  Kernel version:   $FULL_KVER"
echo "  Output dir:       $OUT_DIR"
echo "================================================================"

# ---- 1/3: standalone GRUB EFI binary (unsigned) ---------------------------
echo ""
echo "[bootloader 1/3] Building standalone GRUB EFI binary..."
cd /mnt/intergenos
OUTPUT="$OUT_DIR/grubx64.efi" \
    bash scripts/build-grub-standalone.sh

# ---- 2/3: live initramfs --------------------------------------------------
echo ""
echo "[bootloader 2/3] Building live initramfs..."
INITRAMFS="$OUT_DIR/initramfs.cpio.gz"
INIT_SCRIPT=/mnt/intergenos/installer/init/init.sh \
BUSYBOX=/usr/bin/busybox.static \
MODULES_DIR="/lib/modules/$FULL_KVER" \
    bash /mnt/intergenos/installer/init/build-initramfs.sh "$FULL_KVER" "$INITRAMFS"

# ---- 3/3: UKI (kernel + initramfs + cmdline + os-release, unsigned) -------
echo ""
echo "[bootloader 3/3] Building UKI (Unified Kernel Image)..."
UKI_OUTPUT="$OUT_DIR/igos-live.efi"
VMLINUZ="$VMLINUZ_PATH" \
INITRAMFS="$INITRAMFS" \
CMDLINE=/mnt/intergenos/installer/init/cmdline.txt \
OUTPUT="$UKI_OUTPUT" \
STUB="$STUB" \
    bash /mnt/intergenos/scripts/build-uki.sh

# ---- Summary --------------------------------------------------------------
echo ""
echo "================================================================"
echo "  Bootloader phase: COMPLETE (unsigned artifacts)"
echo "================================================================"
ls -la "$OUT_DIR"
echo ""
echo "Artifacts (all UNSIGNED):"
echo "  $OUT_DIR/grubx64.efi"
echo "  $OUT_DIR/initramfs.cpio.gz"
echo "  $OUT_DIR/igos-live.efi"
echo ""
echo "SHA-256:"
sha256sum "$OUT_DIR/grubx64.efi" "$OUT_DIR/initramfs.cpio.gz" "$OUT_DIR/igos-live.efi" 2>/dev/null \
    | sed 's/^/  /'
echo ""
echo "----------------------------------------------------------------"
echo "  NEXT STEP (operator-paced, OFFLINE):"
echo ""
echo "  1. Copy unsigned artifacts to offline workstation."
echo "  2. Run scripts/sign-release.sh to sign grubx64.efi + igos-live.efi"
echo "     against the InterGenOS vendor cert (NK#1 PIV slot 9c)."
echo "  3. Copy signed artifacts back into chroot at /boot/efi/EFI/igos/"
echo "  4. Resume orchestrator via:"
echo "       sudo bash scripts/build-intergenos.sh --user <user> --start-at image"
echo "----------------------------------------------------------------"
echo ""
echo "Orchestrator may now continue to phase_image (will package whatever"
echo "EFI binaries are present in the chroot — unsigned if signing has not"
echo "yet been done; signed if operator has installed signed artifacts)."
