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
for mode in live install-gui install-tui; do
    cmdline_file="/mnt/intergenos/installer/init/cmdline.${mode}.txt"
    [ -f "$cmdline_file" ] || fail "cmdline missing: $cmdline_file"
done
[ -f /mnt/intergenos/installer/init/build-initramfs.sh ] || fail "initramfs build script missing"
[ -f /mnt/intergenos/installer/init/build-fde-initramfs.sh ] || fail "FDE initramfs build script missing (D-005 Phase D activation)"
[ -f /mnt/intergenos/installer/init/fde-init.sh ]      || fail "fde-init.sh missing (D-005 Phase D foundational artifact)"

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

# ---- 2.5/3: FDE installed-system machinery (D-005 Phase D activation) -----
# Stage fde-init.sh + build-fde-initramfs.sh into the chroot root at
# /usr/lib/intergen/ so the installed system has both the runtime entry
# point (baked into the FDE cpio) and the regen script (called by the
# linux-kernel post-install hook on pkm-upgrade kernel changes). These
# ship to every install regardless of whether LUKS was selected — they
# are inert on plain-install systems (post-install hook detects absent
# /etc/crypttab and skips the FDE path).
#
# Additionally, if cryptsetup-static is present in the chroot
# (packages/core/cryptsetup-static landed by the build-system coordinator's
# Phase D activation lane), bake the initial FDE initramfs cpio against
# the freshly-built kernel so first-install LUKS systems boot without a
# regeneration round-trip. Absent cryptsetup-static, log + skip — Phase
# D activation chain is incomplete and the runtime hook will surface the
# gap; plain installs are unaffected.
echo ""
echo "[bootloader 2.5/3] Staging FDE installed-system machinery..."
mkdir -p /usr/lib/intergen
cp -p /mnt/intergenos/installer/init/fde-init.sh           /usr/lib/intergen/fde-init.sh
cp -p /mnt/intergenos/installer/init/build-fde-initramfs.sh /usr/lib/intergen/build-fde-initramfs.sh
chmod +x /usr/lib/intergen/fde-init.sh /usr/lib/intergen/build-fde-initramfs.sh
echo "  staged: /usr/lib/intergen/fde-init.sh"
echo "  staged: /usr/lib/intergen/build-fde-initramfs.sh"

if [ -x /usr/lib/intergen/cryptsetup-static ]; then
    echo "  cryptsetup-static present — baking FDE initramfs for $FULL_KVER"
    INIT_SCRIPT=/usr/lib/intergen/fde-init.sh \
    BUSYBOX=/usr/bin/busybox.static \
    CRYPTSETUP_STATIC=/usr/lib/intergen/cryptsetup-static \
    MODULES_DIR="/lib/modules/$FULL_KVER" \
        bash /usr/lib/intergen/build-fde-initramfs.sh "$FULL_KVER" \
            /usr/lib/intergen/fde-initramfs.cpio.gz
else
    echo "  cryptsetup-static absent — D-005 Phase D activation chain incomplete; skipping FDE initramfs bake."
    echo "  Plain installs unaffected. LUKS installs will fail to unlock at boot until packages/core/cryptsetup-static lands + chroot rebuild."
fi

# ---- 3/3: UKIs (one per boot mode, kernel + initramfs + per-mode cmdline) ----
# Each UKI carries a sealed `.cmdline` section (igos.mode=...) so the
# boot-mode decision is cryptographically bound to a signed PE — an attacker
# editing ESP-side grub.cfg cannot switch modes. init.sh reads `igos.mode=`
# from /proc/cmdline and dispatches to live / install-gui / install-tui.
echo ""
echo "[bootloader 3/3] Building UKIs (one per boot mode)..."
for mode in live install-gui install-tui; do
    UKI_OUTPUT="$OUT_DIR/igos-${mode}.efi"
    CMDLINE_FILE="/mnt/intergenos/installer/init/cmdline.${mode}.txt"
    echo ""
    echo "  -> mode=${mode}"
    echo "     cmdline: $CMDLINE_FILE"
    echo "     output:  $UKI_OUTPUT"
    VMLINUZ="$VMLINUZ_PATH" \
    INITRAMFS="$INITRAMFS" \
    CMDLINE="$CMDLINE_FILE" \
    OUTPUT="$UKI_OUTPUT" \
    STUB="$STUB" \
        bash /mnt/intergenos/scripts/build-uki.sh
done

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
echo "  $OUT_DIR/igos-install-gui.efi"
echo "  $OUT_DIR/igos-install-tui.efi"
echo ""
echo "SHA-256:"
sha256sum "$OUT_DIR/grubx64.efi" \
          "$OUT_DIR/initramfs.cpio.gz" \
          "$OUT_DIR/igos-live.efi" \
          "$OUT_DIR/igos-install-gui.efi" \
          "$OUT_DIR/igos-install-tui.efi" 2>/dev/null \
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
