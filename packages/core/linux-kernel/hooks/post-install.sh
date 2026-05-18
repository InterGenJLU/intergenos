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
log() { echo "[linux-kernel:post-install] $*" >&2; }

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
UCODE="/boot/intel-ucode.img"
ESP_UKI_DIR="/boot/efi/EFI/Linux"
UKI="$ESP_UKI_DIR/intergenos-$NEW_KVER.efi"
MOK_KEY="/var/lib/intergen/mok/mok.key"
MOK_CERT="/var/lib/intergen/mok/mok.crt"
CMDLINE_FILE="/etc/kernel/cmdline"

# D-005 Phase D: LUKS install detection. Presence of /etc/crypttab implies
# Forge wired LUKS at install time (per D-001 opt-in LUKS-at-install).
# When LUKS install is detected the UKI must bundle the FDE-initramfs
# (busybox + cryptsetup-static + dm_crypt + ext4 + storage drivers + the
# fde-init.sh entry point from installer/init/fde-init.sh) instead of the
# plain-install minimal microcode cpio.
#
# Phase D ACTIVATION DEPENDENCY: the FDE initramfs cpio must be built and
# staged at FDE_INITRD_PATH by build-fde-initramfs.sh (not yet in tree —
# see installer/init/fde-init.sh module header for the full activation
# chain). Until the packager + cryptsetup-static package land, this hook
# detects LUKS, logs the gap, and falls back to the plain UKI build (the
# UKI will boot but without LUKS unlock — recovery via grub-loads-vmlinuz
# with a manually-provided initramfs).
FDE_INITRD_PATH="/usr/lib/intergen/fde-initramfs.cpio.gz"
IS_LUKS_INSTALL="no"
if [ -f /etc/crypttab ] && grep -qE "^[^#]" /etc/crypttab 2>/dev/null; then
    IS_LUKS_INSTALL="yes"
    log "LUKS install detected (/etc/crypttab has active entries)"
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
# Intel microcode: load FIRST so it's applied before kernel init
[ -f "$UCODE" ] && UKIFY_ARGS+=("--initrd=$UCODE")

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
        log "LUKS install but $FDE_INITRD_PATH missing — Phase D activation chain incomplete. UKI will be built WITHOUT FDE initramfs; root unlock at boot will fail until build-fde-initramfs.sh + cryptsetup-static package land. grub-loads-vmlinuz fallback path with manual cryptsetup unlock is the operator recovery."
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
