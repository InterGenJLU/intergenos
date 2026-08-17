#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# update-boot-menu.sh — point the ESP GRUB menu at a newly-installed kernel.
#
# WHY THIS EXISTS. The ESP grub.cfg is composed once at install time and its
# entries reference the install-era kernel release BY NAME — the UKI chainload
# path (/EFI/Linux/intergenos-<kver>.efi), the fallback vmlinuz path, and the
# entry titles. Kernel upgrades mint a new UKI but nothing updated the menu,
# so the default entry kept chainloading the OLD release — whose module tree
# the package replacement had just removed. The retained old UKI still boots
# (a UKI embeds its own kernel), lands in a kernel that can load no modules,
# and the boot drops to emergency mode. (Incident: 2026-07-24, first
# kernel-release transition taken through a fleet upgrade round.)
#
# WHAT IT DOES. Rewrites every kernel-release token (<ver>-igos-<rel>) in the
# menu to the just-installed release, in place, with a backup beside the file.
# This is deliberately a token rewrite rather than a grub-mkconfig regeneration:
# the shipped menu is installer-composed (grub-mkconfig on a target would not
# reproduce the UKI chainload entry), the rewrite is deterministic and
# idempotent, and it is exactly the manual remediation proven on four machines
# during the incident.
#
# Fail-safe: never deletes; missing/unwritable menu or a post-edit verification
# miss exits non-zero so the CALLER can warn loudly — the caller must treat
# menu-update failure as "the next boot may run the previous kernel."
#
# Usage: update-boot-menu.sh <new-kver>
# Env (test override): GRUB_CFG — menu path (default: resolved below).
set -u

# Menu-path resolution. The installer composes the menu at
# /boot/efi/EFI/InterGenOS/grub.cfg (installer/backend/bootloader.py
# ESP_BOOT_DIR — mixed case). The prior default here was the lowercase
# /boot/efi/EFI/intergenos/grub.cfg, which exists on NO installed system, so
# every post-install kernel update failed its menu update (measured live on a
# ge9b-12 install trace, 2026-07-30). Resolution order, fail-closed:
#   1. $GRUB_CFG when the caller/tests set it — used as given.
#   2. The canonical mixed-case path.
#   3. Discovery: exactly ONE */grub.cfg under the ESP EFI dir (any case).
#      Zero or several → refuse; never guess between menus.
if [ -z "${GRUB_CFG:-}" ]; then
    if [ -f /boot/efi/EFI/InterGenOS/grub.cfg ]; then
        GRUB_CFG=/boot/efi/EFI/InterGenOS/grub.cfg
    else
        _found=$(find /boot/efi/EFI -mindepth 2 -maxdepth 2 -name grub.cfg 2>/dev/null)
        if [ "$(printf '%s\n' "$_found" | grep -c .)" = 1 ]; then
            GRUB_CFG="$_found"
        else
            GRUB_CFG=/boot/efi/EFI/InterGenOS/grub.cfg   # canonical, for the message below
        fi
    fi
fi
NEW_KVER="${1:-}"

_m() { echo "[update-boot-menu] $*"; }

if [ -z "$NEW_KVER" ]; then
    _m "ERROR: no kernel release argument given"
    exit 1
fi
case "$NEW_KVER" in
    *-igos-*) : ;;
    *) _m "ERROR: '$NEW_KVER' does not look like an InterGenOS kernel release (<ver>-igos-<rel>)"; exit 1 ;;
esac

if [ ! -f "$GRUB_CFG" ]; then
    _m "ERROR: menu not found at $GRUB_CFG — boot entries NOT updated."
    _m "During an INITIAL install this is expected ordering: the installer composes the menu AFTER package hooks run, from the installed kernel, so nothing is lost. On a RUNNING system this is a real failure — the next boot may chainload a previous kernel; fix by re-running this script once the menu exists."
    exit 1
fi

# Kernel-release tokens in the menu: <digits><ver-chars>-igos-<digits>.
TOKEN_RE='[0-9][0-9a-zA-Z.]*-igos-[0-9]\{1,\}'

if ! grep -q -- "-igos-" "$GRUB_CFG"; then
    _m "ERROR: $GRUB_CFG contains no InterGenOS kernel-release tokens — refusing to edit an unrecognized menu"
    exit 1
fi

# Idempotence fast-path: nothing to change.
if ! grep -e "$TOKEN_RE" "$GRUB_CFG" | grep -qv -F "$NEW_KVER"; then
    _m "menu already points at $NEW_KVER — nothing to do"
    exit 0
fi

BAK="$GRUB_CFG.bak-$NEW_KVER"
if ! cp -p "$GRUB_CFG" "$BAK"; then
    _m "ERROR: cannot write backup $BAK — refusing to edit without one"
    exit 1
fi

if ! sed -i "s/$TOKEN_RE/$NEW_KVER/g" "$GRUB_CFG"; then
    _m "ERROR: edit failed — restoring from $BAK"
    cp -p "$BAK" "$GRUB_CFG"
    exit 1
fi

# Post-edit verification: the chainloaded UKI reference must now be the new
# release, and no other release token may remain.
if ! grep -q "intergenos-$NEW_KVER.efi" "$GRUB_CFG"; then
    _m "ERROR: post-edit menu lacks intergenos-$NEW_KVER.efi — restoring from $BAK"
    cp -p "$BAK" "$GRUB_CFG"
    exit 1
fi
if grep -e "$TOKEN_RE" "$GRUB_CFG" | grep -qv -F "$NEW_KVER"; then
    _m "ERROR: stale kernel-release tokens survived the edit — restoring from $BAK"
    cp -p "$BAK" "$GRUB_CFG"
    exit 1
fi

_m "menu updated: all entries now reference $NEW_KVER (backup at $BAK)"
exit 0
