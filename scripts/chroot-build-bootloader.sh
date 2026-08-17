#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# chroot-build-bootloader.sh — assemble unsigned bootloader artifacts inside chroot.
#
# Runs INSIDE the chroot (invoked by phase_bootloader via chroot-enter.sh).
#
# Produces the unsigned non-UKI bootloader artifacts under
# /mnt/intergenos/build/bootloader/:
#   - grubx64.efi       (standalone GRUB EFI binary, unsigned)
#   - initramfs.cpio.gz (live initramfs with busybox-static + custom init + kernel modules)
#   - intel-ucode.img   (microcode early-load cpio, optional per host CPU)
#   - amd-ucode.img     (microcode early-load cpio, optional per host CPU)
#
# UKI builds intentionally moved to phase_ukis_verity (lever-4 dm-verity,
# 2026-05-28). The live-mode UKI's sealed cmdline must include the verity
# root hash, but the root hash is only known AFTER phase_squashfs runs
# `veritysetup format`. UKIs (live + install-gui + install-tui) are built
# from the host AFTER phase_squashfs emits the verity hashtree + params,
# using scripts/build-ukis-verity.sh against the artifacts staged here.
#
# Unsigned artifacts are signed OFFLINE via scripts/sign-release.sh in a
# separate operator workflow (NK#1 PIV slot 9c). The signing ceremony
# now happens after phase_ukis_verity (not after phase_bootloader) so
# grub + all 3 UKIs are signed in a single session.
#
# Operator workflow:
#   1. Run build-intergenos.sh                              (full pipeline)
#   2. Pipeline halts after phase_ukis_verity with unsigned artifacts ready
#   3. Run sign-release.sh on offline workstation
#   4. Place .efi.signed files in /mnt/intergenos/build/bootloader/
#   5. Resume: build-intergenos.sh --start-at iso

set -euo pipefail

# ---- Resolve kernel version from the kernel package metadata --------------
KVER_FILE=/mnt/intergenos/packages/core/linux-kernel/package.yml
if [ ! -f "$KVER_FILE" ]; then
    echo "error: kernel package metadata not found: $KVER_FILE" >&2
    echo "       chroot may not have packages/ synced (sync_chroot_scripts in orchestrator)." >&2
    exit 1
fi
KVER=$(grep '^version:' "$KVER_FILE" | awk '{print $2}' | tr -d '"')
KREL=$(grep '^release:' "$KVER_FILE" | awk '{print $2}' | tr -d '"')
# CONFIG_LOCALVERSION is release-stamped to -igos-<release> at kernel build time
# (packages/core/linux-kernel/build.sh), so the modules dir / vmlinuz / UKI all
# carry the release; FULL_KVER must match it to find the installed kernel.
FULL_KVER="${KVER}-igos-${KREL:-1}"

OUT_DIR=/mnt/intergenos/build/bootloader
mkdir -p "$OUT_DIR"

# Source the forensic-trace bash companion (no-op when verbose unset).
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_init "tier-bootloader"
    _BL_TIER_START_MS=$(date +%s%3N)
    trace_event tier_start tier=bootloader kver="$FULL_KVER" out_dir="$OUT_DIR"
    _bl_trace_exit() {
        local rc=$?
        trace_event tier_end tier=bootloader rc::=$rc duration_ms::=$(( $(date +%s%3N) - _BL_TIER_START_MS ))
        trace_close
        return $rc
    }
    trap _bl_trace_exit EXIT
fi

# Shared build-output library — one house style across the shell pipeline.
# This script's fail() below keeps its own build_failure_emit side effect.
# shellcheck source=lib/logging.sh
[ -f /mnt/intergenos/scripts/lib/logging.sh ] && source /mnt/intergenos/scripts/lib/logging.sh

# ---- Sanity-check the prerequisites the sub-scripts will need -------------
fail() {
    echo "error: $*" >&2
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        build_failure_emit --where chroot-build-bootloader.sh:fail --why "$*" --phase bootloader
    fi
    exit 1
}

[ -x /usr/bin/busybox.static ] || fail "busybox-static not installed in chroot. Build packages/core/busybox-static first."

VMLINUZ_PATH="/boot/vmlinuz-$FULL_KVER"
[ -f "$VMLINUZ_PATH" ] || fail "kernel image not found: $VMLINUZ_PATH (kernel phase did not install? check phase_kernel output)"

[ -d "/lib/modules/$FULL_KVER" ] || fail "kernel modules not found: /lib/modules/$FULL_KVER"

# Existence alone is not enough: a superseded kernel release's orphaned
# twin (release-named module tree + vmlinuz) passes the two checks above
# while squashfs would ship both. Assert exclusivity + recipe agreement
# (decided gate wave, 2026-07-12).
bash /mnt/intergenos/scripts/preflight-single-kernel.sh --expect "$FULL_KVER" \
    || fail "staged-kernel exclusivity violated (superseded kernel twin present — see listing above)"

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

echo ">>> Bootloader phase: unsigned artifact assembly"
echo "    Kernel version:   $FULL_KVER"
echo "    Output dir:       $OUT_DIR"

# ---- 1/3: standalone GRUB EFI binary (unsigned) ---------------------------
echo ""
echo "[bootloader 1/3] Building standalone GRUB EFI binary..."
cd /mnt/intergenos
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_event bootloader_step step="grub_standalone" output="$OUT_DIR/grubx64.efi"
fi
OUTPUT="$OUT_DIR/grubx64.efi" \
    bash scripts/build-grub-standalone.sh

# ---- 2/3: live initramfs --------------------------------------------------
echo ""
echo "[bootloader 2/3] Building live initramfs..."
INITRAMFS="$OUT_DIR/initramfs.cpio.gz"
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_event bootloader_step step="live_initramfs" output="$INITRAMFS" kver="$FULL_KVER"
fi
INIT_SCRIPT=/mnt/intergenos/installer/init/init.sh \
BUSYBOX=/usr/bin/busybox.static \
MODULES_DIR="/lib/modules/$FULL_KVER" \
    bash /mnt/intergenos/installer/init/build-initramfs.sh "$FULL_KVER" "$INITRAMFS"

# ---- 2.5/3: FDE installed-system machinery (D-005 Phase D activation) -----
# The four kernel-lifecycle helpers (fde-init.sh, build-fde-initramfs.sh,
# build-microcode-cpio.sh, prune-old-kernels.sh) are PACKAGE-OWNED by
# packages/core/linux-kernel (r4) — installed to /usr/lib/intergen/ when
# the kernel package deploys, proven by its verify_paths at the squashfs
# gate. This phase used to stage divergent copies here; a chroot lineage
# that skipped this phase shipped without them entirely (ge9b-01: no
# kernel pruning, BIOS microcode, FDE Gap B live). One owner now — this
# phase only VERIFIES presence, loudly, before consuming them below.
#
# If cryptsetup-static is present in the chroot (packages/core/
# cryptsetup-static, Phase D activation lane), bake the initial FDE
# initramfs cpio against the freshly-built kernel so first-install LUKS
# systems boot without a regeneration round-trip. Absent cryptsetup-static,
# log + skip — plain installs are unaffected.
echo ""
echo "[bootloader 2.5/3] Verifying package-owned FDE/kernel-lifecycle helpers..."
for helper in fde-init.sh build-fde-initramfs.sh build-microcode-cpio.sh prune-old-kernels.sh; do
    if [ ! -x "/usr/lib/intergen/$helper" ]; then
        echo "FATAL: /usr/lib/intergen/$helper is absent or not executable." >&2
        echo "  These are installed by packages/core/linux-kernel (release >= 4)." >&2
        echo "  This chroot's kernel package predates r4 — rebuild linux-kernel" >&2
        echo "  before phase_bootloader. Refusing to continue (finding #4: a" >&2
        echo "  missing helper means no pruning / BIOS microcode / unbootable FDE)." >&2
        exit 1
    fi
    echo "  verified: /usr/lib/intergen/$helper (package-owned)"
done

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
    # B4 (USA-1 audit S-W2 closure): IGOS_REQUIRE_FDE=1 promotes the silent
    # degradation to a hard build failure. Mirrors the UNSIGNED_TEST=1
    # opt-out pattern at phase_bootloader (build-intergenos.sh:1067-1080):
    # default behavior preserves dev iteration, operator-set env enforces
    # production posture. Set IGOS_REQUIRE_FDE=1 for signed-release builds
    # so LUKS installs can never silently ship unable to unlock.
    if [ "${IGOS_REQUIRE_FDE:-0}" = "1" ]; then
        echo "  IGOS_REQUIRE_FDE=1 set — failing build (cryptsetup-static is required)"
        exit 1
    fi
fi

# ---- 2.7/3: Microcode early-load cpios (Intel + AMD) ----------------------
# Generate the microcode cpio archives that will be bundled into each UKI's
# .initrd section. Audit row T0-3 #5 "Microcode early-load never reaches
# the UKI" (matrix 2026-05-18 line 583): the previous single-arg
# `--initrd=$INITRAMFS` call to build-uki.sh skipped microcode entirely on
# both live-ISO and initial-install UKIs. Reconciliation item G "AMD
# microcode path" lands here too — same helper produces both Intel + AMD
# cpios from linux-firmware blobs that are already present in the chroot.
#
# The cpios are kept under $OUT_DIR (alongside the UKIs themselves) so the
# whole bootloader artifact set lives in one directory for the signing
# ceremony + ISO assembly to pull from. Per-host CPU-vendor selection is
# the kernel's job — including both cpios in every UKI is the canonical
# pattern (matches Arch mkinitcpio's ALL_microcode = (intel-ucode amd-ucode)
# default + Fedora's dracut microcode_ctl module behavior).
echo ""
echo "[bootloader 2.7/3] Building microcode early-load cpios..."
OUTPUT_DIR="$OUT_DIR" \
    bash /mnt/intergenos/scripts/build-microcode-cpio.sh

# Compose the MICROCODE env var for build-uki.sh from whichever cpios were
# produced (helper omits missing-firmware cases). Order: Intel first, then
# AMD — matches the kernel's expected scan order and Arch's ALL_microcode
# default ordering.
MICROCODE_ARGS=""
[ -f "$OUT_DIR/intel-ucode.img" ] && MICROCODE_ARGS="$MICROCODE_ARGS $OUT_DIR/intel-ucode.img"
[ -f "$OUT_DIR/amd-ucode.img" ]   && MICROCODE_ARGS="$MICROCODE_ARGS $OUT_DIR/amd-ucode.img"
# Trim leading whitespace; build-uki.sh's `for ucode in $MICROCODE` is
# space-tolerant but the printed log line "${MICROCODE:-<none>}" looks
# cleaner without the leading space.
MICROCODE_ARGS="${MICROCODE_ARGS# }"
echo "  microcode list passed to UKI build: ${MICROCODE_ARGS:-<none>}"

# ---- Stage VMLINUZ for the host-side phase_ukis_verity --------------------
# phase_ukis_verity (post-squashfs) runs scripts/build-ukis-verity.sh from
# the HOST, which needs the chroot's vmlinuz + initramfs + microcode cpios.
# initramfs.cpio.gz + intel-ucode.img + amd-ucode.img are already at $OUT_DIR
# (host-visible after orchestrator copies $OUT_DIR/* out). Stage the kernel
# image alongside so the host has the complete UKI-input set in one place.
echo ""
echo "[bootloader 3/3] Staging vmlinuz for host-side UKI build..."
cp -p "$VMLINUZ_PATH" "$OUT_DIR/vmlinuz"
echo "  staged: $OUT_DIR/vmlinuz ($(stat -c%s "$VMLINUZ_PATH") bytes)"

# Also stage /etc/os-release — build-uki.sh embeds it in the UKI's .osrel
# section. The host-side build needs access to it without entering the chroot.
cp -p /etc/os-release "$OUT_DIR/os-release"
echo "  staged: $OUT_DIR/os-release"

# ---- Summary --------------------------------------------------------------
echo ""
echo ">>> Bootloader phase complete (unsigned pre-UKI artifacts)"
ls -la "$OUT_DIR"
echo ""
echo "Artifacts (all UNSIGNED, UKIs built by phase_ukis_verity):"
echo "  $OUT_DIR/grubx64.efi"
echo "  $OUT_DIR/initramfs.cpio.gz"
echo "  $OUT_DIR/vmlinuz"
echo "  $OUT_DIR/os-release"
[ -f "$OUT_DIR/intel-ucode.img" ] && echo "  $OUT_DIR/intel-ucode.img"
[ -f "$OUT_DIR/amd-ucode.img" ]   && echo "  $OUT_DIR/amd-ucode.img"
echo ""
echo "SHA-256:"
sha256sum "$OUT_DIR/grubx64.efi" \
          "$OUT_DIR/initramfs.cpio.gz" \
          "$OUT_DIR/vmlinuz" 2>/dev/null \
    | sed 's/^/  /'
echo ""
echo "Orchestrator continues to phase_image. UKI build happens in"
echo "phase_ukis_verity after phase_squashfs emits the verity hashtree."
