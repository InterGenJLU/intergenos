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
# reproduce what the installer adds after composing it, its theme block among
# them), the rewrite is deterministic and idempotent, and it is exactly the
# manual remediation proven on four machines during the incident.
#
# A MENU WITH NO KERNEL AT ALL is the one case a rewrite cannot fix: there is no
# token to rewrite. The installer composes the menu with grub-mkconfig, and
# /etc/grub.d/06_uki emits the unified-kernel entries from the images on the ESP
# at that moment; a menu composed before the image existed has that generator's
# section and nothing in it (a real R001.3 install, 2026-10-16: the installed
# menu's only entry was the firmware's, and the machine had nothing of its own
# to boot). When the menu is recognisably this system's own composition — the
# generator's section markers are both there — the section is filled with what
# the same generator emits now, and nothing else in the menu changes: not the
# header, not the theme, not another system's entries. A menu without those
# markers is still refused as unrecognised.
#
# Fail-safe: never deletes; missing/unwritable menu or a post-edit verification
# miss exits non-zero so the CALLER can warn loudly — the caller must treat
# menu-update failure as "the next boot may run the previous kernel."
#
# Usage: update-boot-menu.sh <new-kver>
#        update-boot-menu.sh --print-menu-path
# Env (test overrides): GRUB_CFG — menu path (default: resolved below);
#        UKI_GENERATOR — the unified-kernel menu generator (/etc/grub.d/06_uki);
#        ESP_UKI_DIR — where the boot images are (/boot/efi/EFI/Linux).
set -u

UKI_GENERATOR="${UKI_GENERATOR:-/etc/grub.d/06_uki}"
ESP_UKI_DIR="${ESP_UKI_DIR:-/boot/efi/EFI/Linux}"
UKI_SECTION_BEGIN='### BEGIN /etc/grub.d/06_uki ###'
UKI_SECTION_END='### END /etc/grub.d/06_uki ###'

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

# --print-menu-path: print the menu path resolved above and exit. The kernel
# post-install hook calls this so its operator-facing messages name the path
# THIS script resolved, instead of carrying a second copy that can drift from
# it — which is exactly what had happened: the hook printed a lowercase
# /boot/efi/EFI/intergenos/grub.cfg that exists on no installed system. Reads
# nothing and writes nothing, so it is safe to call from a failure path.
if [ "${1:-}" = "--print-menu-path" ]; then
    echo "$GRUB_CFG"
    exit 0
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

# Fill this system's own unified-kernel section with what its generator emits
# now (see A MENU WITH NO KERNEL AT ALL, above). Exits.
compose_uki_section() {
    local image="$ESP_UKI_DIR/intergenos-$NEW_KVER.efi"
    local entry_line="menuentry \"InterGenOS $NEW_KVER (UKI)\" --id intergenos-uki "
    local chain_line="    chainloader /EFI/Linux/intergenos-$NEW_KVER.efi"
    if [ ! -f "$image" ]; then
        _m "ERROR: $GRUB_CFG has no InterGenOS entry, and there is no boot image for $NEW_KVER at $image to give it one — nothing composed"
        exit 1
    fi
    if [ ! -x "$UKI_GENERATOR" ]; then
        _m "ERROR: $GRUB_CFG has no InterGenOS entry, and the unified-kernel menu generator $UKI_GENERATOR is not there to compose one — nothing composed"
        exit 1
    fi
    local section
    if ! section=$("$UKI_GENERATOR" 2>/dev/null); then
        _m "ERROR: $UKI_GENERATOR failed — nothing composed"
        exit 1
    fi
    if ! printf '%s\n' "$section" | grep -qF -- "$entry_line" ||
       ! printf '%s\n' "$section" | grep -qxF -- "$chain_line"; then
        _m "ERROR: $UKI_GENERATOR emitted no default entry for intergenos-$NEW_KVER.efi — nothing composed"
        exit 1
    fi

    BAK="$GRUB_CFG.bak-$NEW_KVER"
    if ! cp -p "$GRUB_CFG" "$BAK"; then
        _m "ERROR: cannot write backup $BAK — refusing to edit without one"
        exit 1
    fi
    local sect_file="$GRUB_CFG.uki-section.$$" new_file="$GRUB_CFG.composed.$$"
    if ! printf '%s\n' "$section" > "$sect_file" ||
       ! awk -v begin="$UKI_SECTION_BEGIN" -v end="$UKI_SECTION_END" -v sect="$sect_file" '
            $0 == begin { print; while ((getline line < sect) > 0) print line; skip = 1; next }
            $0 == end   { skip = 0 }
            !skip       { print }' "$BAK" > "$new_file" ||
       ! cp "$new_file" "$GRUB_CFG"; then
        rm -f "$sect_file" "$new_file"
        _m "ERROR: composing the entry failed — restoring from $BAK"
        cp -p "$BAK" "$GRUB_CFG"
        exit 1
    fi
    rm -f "$sect_file" "$new_file"

    # Post-edit verification: the default entry is this release's and chainloads
    # its image; everything outside the section is byte for byte what it was.
    if ! grep -qF -- "$entry_line" "$GRUB_CFG" || ! grep -qxF -- "$chain_line" "$GRUB_CFG"; then
        _m "ERROR: post-edit menu lacks the default entry for intergenos-$NEW_KVER.efi — restoring from $BAK"
        cp -p "$BAK" "$GRUB_CFG"
        exit 1
    fi
    if ! cmp -s <(awk -v b="$UKI_SECTION_BEGIN" -v e="$UKI_SECTION_END" '$0==b{s=1;next} $0==e{s=0;next} !s' "$BAK") \
                <(awk -v b="$UKI_SECTION_BEGIN" -v e="$UKI_SECTION_END" '$0==b{s=1;next} $0==e{s=0;next} !s' "$GRUB_CFG"); then
        _m "ERROR: composing the entry changed the menu outside its own section — restoring from $BAK"
        cp -p "$BAK" "$GRUB_CFG"
        exit 1
    fi
    _m "menu composed: it had no InterGenOS entry, and now boots $NEW_KVER by default (backup at $BAK)"
    exit 0
}

if ! grep -q -- "-igos-" "$GRUB_CFG"; then
    if grep -qxF -- "$UKI_SECTION_BEGIN" "$GRUB_CFG" && grep -qxF -- "$UKI_SECTION_END" "$GRUB_CFG"; then
        compose_uki_section
    fi
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
