#!/bin/bash
# linux-kernel post-install hook — D-005 Phase A UKI rebuild + sign-with-user-MOK
#
# Fires at RUNTIME after pkm deploys a new linux-kernel package archive
# (Forge install on first-boot OR `pkm upgrade linux-kernel` on a live
# installed system). Picks up the just-installed vmlinuz from /boot/ +
# bundles it as a UKI under /boot/efi/EFI/Linux/ signed with the user's
# local MOK keypair per D-005 Option A.
#
# pkm provides env: PKM_PACKAGE_NAME, PKM_PACKAGE_VERSION, PKM_PACKAGE_ROOT.
#
# Phase A scope (this hook):
#   - Build UKI from new vmlinuz + intel-ucode.img + initramfs.img + cmdline
#   - Sign UKI with /var/lib/intergen/mok/mok.{key,crt} if present
#   - Install UKI to /boot/efi/EFI/Linux/intergenos-<kver>.efi
#   - Gracefully degrade (exit 0, no break) if ukify or MOK absent —
#     grub-loads-vmlinuz path stays intact as fallback
#
# Phase B (TBD): explicit fallback grub menuentry + ESP-size enforcement.
# Phase C (TBD): per-machine MOK keypair regen at install + MokManager UX.
# Phase D (TBD): LUKS-case busybox+cryptsetup FDE initramfs + plain-install
#                minimal initramfs (--initrd= bundling diverges by install type).
#
# B-041 cmdline drift: resolves via UKI .cmdline section bundling (sourced
# from /etc/kernel/cmdline; falls back to /proc/cmdline if unset).

set -uo pipefail

# Dedicated persistent log file for kernel install/upgrade operations.
# Kernel install is security-critical (UKI signing, FDE initramfs regen,
# Secure Boot composition) and users + ops need a stable place to look
# when boot-time troubleshooting (per docs/users/full-disk-encryption.md
# troubleshooting table). log() writes to BOTH stderr (so pkm's terminal
# / journalctl surface keeps showing it live) and this file (so a
# subsequent boot or recovery session can read the trail without re-
# running the failing operation). Append-only; rotation is left to the
# host's standard logrotate stack.
LOGFILE=/var/log/intergen-kernel-postinstall.log
mkdir -p "$(dirname "$LOGFILE")" 2>/dev/null || true

log() {
    local msg="[linux-kernel:post-install] $*"
    echo "$msg" >&2
    # Best-effort persistent append. Never let a logging failure
    # break the install — chmod issues, full-disk, ESP-mounted-ro,
    # etc. silently fall through to stderr-only.
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') $msg" >> "$LOGFILE" 2>/dev/null || true
}

# Identify the newly-installed kernel — pick the most-recently-modified
# /boot/vmlinuz-*-igos image. Forge install + pkm install land vmlinuz
# at /boot/vmlinuz-<version>-igos per the package's do_install convention.
NEW_KVER=$(ls -t /boot/vmlinuz-*-igos 2>/dev/null | head -1 | sed 's|.*/vmlinuz-||')
if [ -z "$NEW_KVER" ]; then
    log "no /boot/vmlinuz-*-igos found; nothing to do"
    exit 0
fi

log "regenerating UKI for kernel $NEW_KVER (PKM package: ${PKM_PACKAGE_NAME:-?} ${PKM_PACKAGE_VERSION:-?})"

VMLINUZ="/boot/vmlinuz-$NEW_KVER"
INITRD="/boot/initramfs.img"
UCODE_INTEL="/boot/intel-ucode.img"
UCODE_AMD="/boot/amd-ucode.img"
ESP_UKI_DIR="/boot/efi/EFI/Linux"
UKI="$ESP_UKI_DIR/intergenos-$NEW_KVER.efi"
MOK_KEY="/var/lib/intergen/mok/mok.key"
MOK_CERT="/var/lib/intergen/mok/mok.crt"
CMDLINE_FILE="/etc/kernel/cmdline"
MICROCODE_HELPER="/usr/lib/intergen/build-microcode-cpio.sh"

# Regenerate microcode early-load cpios. The intel-ucode package's
# post-install would already have written /boot/intel-ucode.img after its
# own install, but linux-firmware ships AMD microcode blobs without a
# parallel cpio-generation step — the helper covers both vendors and is
# idempotent. Best-effort: failure here drops back to whatever cpios
# (if any) already exist at /boot/ from prior runs or intel-ucode's
# install-time generation.
if [ -x "$MICROCODE_HELPER" ]; then
    log "regenerating microcode early-load cpios via $MICROCODE_HELPER"
    if OUTPUT_DIR=/boot "$MICROCODE_HELPER" >/dev/null 2>&1; then
        log "microcode cpios refreshed (intel=$( [ -f "$UCODE_INTEL" ] && echo yes || echo no ), amd=$( [ -f "$UCODE_AMD" ] && echo yes || echo no ))"
    else
        log "WARNING: $MICROCODE_HELPER failed (exit $?) — falling back to existing /boot/{intel,amd}-ucode.img if present"
    fi
else
    log "$MICROCODE_HELPER absent — using existing /boot/{intel,amd}-ucode.img if present (intel-ucode package may have written intel-ucode.img; amd-ucode.img is only produced when the helper is staged)"
fi

# D-005 Phase D: LUKS install detection. Presence of /etc/crypttab implies
# Forge wired LUKS at install time (per D-001 opt-in LUKS-at-install).
# When LUKS install is detected the UKI must bundle the FDE-initramfs
# (busybox + cryptsetup-static + dm_crypt + ext4 + storage drivers + the
# fde-init.sh entry point from installer/init/fde-init.sh) instead of the
# plain-install minimal microcode cpio.
#
# Phase D activation chain (now landed):
#   /usr/lib/intergen/build-fde-initramfs.sh   — the packager (staged by
#                                                scripts/chroot-build-bootloader.sh)
#   /usr/lib/intergen/fde-init.sh              — the runtime entry point
#                                                (staged by chroot-build)
#   /usr/lib/intergen/cryptsetup-static        — statically-linked
#                                                cryptsetup binary (output
#                                                of packages/core/cryptsetup-static)
#   /usr/lib/intergen/fde-initramfs.cpio.gz    — the cpio this hook bundles
#                                                (regenerated per-kernel
#                                                on every hook fire so pkm
#                                                upgrade of linux-kernel
#                                                stays in sync with new
#                                                /lib/modules/$KVER)
#
# Regeneration on every hook fire ensures freshness across pkm-upgrade
# kernel changes (the build-time cpio's modules would be stale otherwise).
# If the packager or cryptsetup-static are absent (Phase D activation
# chain incomplete on this host), the existing /usr/lib/intergen/
# fde-initramfs.cpio.gz (if any) is used as-is; if no cpio exists, the
# UKI is built without an FDE initramfs and the LUKS install will fail
# to unlock at boot — recovery via grub-loads-vmlinuz with a manually-
# provided initramfs per D-005 fallback semantics.
FDE_INITRD_PATH="/usr/lib/intergen/fde-initramfs.cpio.gz"
FDE_BUILDER="/usr/lib/intergen/build-fde-initramfs.sh"
CRYPTSETUP_STATIC="/usr/lib/intergen/cryptsetup-static"
IS_LUKS_INSTALL="no"
if [ -f /etc/crypttab ] && grep -qE "^[^#]" /etc/crypttab 2>/dev/null; then
    IS_LUKS_INSTALL="yes"
    log "LUKS install detected (/etc/crypttab has active entries)"

    # Regenerate FDE initramfs against the freshly-installed kernel's
    # /lib/modules/$NEW_KVER. Required for pkm-upgrade correctness;
    # build-time cpio is keyed to the original install's KVER and would
    # ship stale modules after kernel upgrade. Graceful degrade if the
    # activation-chain pieces are absent — fall through to whatever
    # cpio (if any) exists at FDE_INITRD_PATH.
    if [ -x "$FDE_BUILDER" ] && [ -x "$CRYPTSETUP_STATIC" ]; then
        log "regenerating FDE initramfs for $NEW_KVER via $FDE_BUILDER"
        if INIT_SCRIPT=/usr/lib/intergen/fde-init.sh \
           BUSYBOX=/usr/bin/busybox.static \
           CRYPTSETUP_STATIC="$CRYPTSETUP_STATIC" \
           MODULES_DIR="/lib/modules/$NEW_KVER" \
           "$FDE_BUILDER" "$NEW_KVER" "$FDE_INITRD_PATH" >/dev/null 2>&1; then
            log "FDE initramfs regenerated at $FDE_INITRD_PATH"
        else
            log "WARNING: $FDE_BUILDER failed (exit $?) — using existing $FDE_INITRD_PATH if present"
        fi
    else
        log "Phase D activation chain incomplete on this host (missing $FDE_BUILDER or $CRYPTSETUP_STATIC) — using existing $FDE_INITRD_PATH if present"
    fi
fi

# Required tool: ukify (ships with systemd; systemd-pass2 has -D ukify=enabled).
# If absent, the system is on the pre-D-005 grub-loads-vmlinuz path (which
# D-005 explicitly preserves as recovery-fallback per directive). Bail clean.
if ! command -v ukify >/dev/null 2>&1; then
    log "ukify not on PATH — install ships systemd without UKI builder; grub-loads-vmlinuz path remains canonical for this host. Skipping UKI generation."
    exit 0
fi

# Determine cmdline (B-041 resolution: cmdline travels with UKI .cmdline section).
if [ -f "$CMDLINE_FILE" ]; then
    CMDLINE=$(< "$CMDLINE_FILE")
elif [ -f /proc/cmdline ]; then
    CMDLINE=$(< /proc/cmdline)
    # Strip kernel-injected items (root=, ro/rw, initrd=) that ukify regenerates
    CMDLINE=$(echo "$CMDLINE" | tr ' ' '\n' | grep -v -E '^(BOOT_IMAGE|initrd)=' | tr '\n' ' ')
    log "no /etc/kernel/cmdline; sourced from /proc/cmdline (stripped boot-time injections)"
else
    log "no cmdline source (/etc/kernel/cmdline or /proc/cmdline) — aborting UKI generation"
    exit 0
fi

# Ensure ESP UKI directory exists
if ! mkdir -p "$ESP_UKI_DIR" 2>/dev/null; then
    log "cannot create $ESP_UKI_DIR — ESP not mounted? Aborting UKI generation; grub-loads-vmlinuz fallback applies."
    exit 0
fi

# Build ukify args
UKIFY_ARGS=(
    "build"
    "--linux=$VMLINUZ"
    "--cmdline=$CMDLINE"
    "--output=$UKI"
)
# Microcode: load FIRST (each --initrd= in declaration order) so the
# selected blob is applied before kernel init touches CPU features. Order:
# Intel then AMD — matches scripts/chroot-build-bootloader.sh's order
# (chosen to mirror Arch mkinitcpio's ALL_microcode default ordering). The
# kernel scans the concatenated microcode cpio at early-firmware load and
# picks the blob matching the running CPU vendor; including both is the
# canonical pattern for installation media + system images intended to
# boot on either Intel or AMD silicon.
[ -f "$UCODE_INTEL" ] && UKIFY_ARGS+=("--initrd=$UCODE_INTEL")
[ -f "$UCODE_AMD" ]   && UKIFY_ARGS+=("--initrd=$UCODE_AMD")

# D-005 Phase D: initramfs selection.
# LUKS install: bundle the FDE-initramfs cpio (fde-init.sh + busybox +
#   cryptsetup-static + dm_crypt + ext4 + storage drivers) — required for
#   the kernel to unlock the encrypted root before switch_root.
# Plain install: kernel-builtin storage drivers + PARTUUID + rootwait
#   handle root mount (per 2026-04-09 ratification narrowed by D-005);
#   the only initramfs bundled is the optional /boot/initramfs.img if
#   one was produced by Forge (which on plain installs is minimal /
#   empty — present for the few edge-case modules that benefit from
#   early-userspace handling without making the kernel-builtin set even
#   larger).
if [ "$IS_LUKS_INSTALL" = "yes" ]; then
    if [ -f "$FDE_INITRD_PATH" ]; then
        UKIFY_ARGS+=("--initrd=$FDE_INITRD_PATH")
        log "bundling FDE initramfs ($FDE_INITRD_PATH) into UKI"
    else
        log "LUKS install but $FDE_INITRD_PATH missing AND regen-chain absent. UKI will be built WITHOUT FDE initramfs; root unlock at boot will fail. grub-loads-vmlinuz fallback path with manual cryptsetup unlock is the operator recovery per D-005 fallback semantics."
    fi
else
    # Plain install — generic initramfs.img is optional
    [ -f "$INITRD" ] && UKIFY_ARGS+=("--initrd=$INITRD")
fi

# Sign with user MOK if present (D-005 user-MOK signing model — InterGenOS
# PIV slot 9c key NEVER touches user systems; only the user's local MOK).
if [ -f "$MOK_KEY" ] && [ -f "$MOK_CERT" ]; then
    UKIFY_ARGS+=(
        "--secureboot-private-key=$MOK_KEY"
        "--secureboot-certificate=$MOK_CERT"
    )
    log "signing UKI with user MOK"
else
    log "no user MOK at $MOK_KEY — UKI built unsigned. Secure Boot disabled? OK. Secure Boot enabled with MokManager-enrolled MOK? would refuse to load — re-run install to regenerate MOK."
fi

# Build the UKI
if ukify "${UKIFY_ARGS[@]}" >/dev/null 2>&1; then
    UKI_SIZE=$(stat -c %s "$UKI" 2>/dev/null || echo "?")
    log "UKI built at $UKI ($UKI_SIZE bytes)"
else
    log "ukify failed (exit $?) — UKI generation skipped; grub-loads-vmlinuz path intact as fallback per D-005 recovery semantics."
    exit 0  # NEVER break the kernel install on UKI failure
fi

log "D-005 Phase A complete for kernel $NEW_KVER"
exit 0
