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
# AN EMPTY UNIFIED-KERNEL SECTION is the one case a rewrite cannot fix: there is
# no entry in it to rewrite. The installer composes the menu with grub-mkconfig,
# and /etc/grub.d/06_uki emits the unified-kernel entries from the images on the
# ESP at that moment; a menu composed before the image existed has that
# generator's section and nothing in it (a real R001.3 install, 2026-10-16: the
# installed menu's only entry was the firmware's, and the machine had nothing of
# its own to boot). When the menu is recognisably this system's own composition
# — the generator's section markers are both there, once each — the section is
# filled with what the same generator emits now, WHATEVER the other sections
# hold: a plain install keeps the stock Linux entries beside an empty section,
# and those entries' kernel-release tokens used to send this script down the
# rewrite path, which reported "nothing to do" with no unified-kernel entry, or
# refused on a release change. After the section is filled, entries outside it
# that still name an earlier release are pointed at this one, outside the
# section only; nothing else in the menu changes: not the header, not the
# theme, not another system's entries. A menu without those markers and with no
# kernel-release token is still refused as unrecognised.
#
# NAMING THE RELEASE IS NOT BOOTING IT. A menu whose release tokens already all
# name the new release is left alone only when its pinned default entry (the
# entry with --id intergenos-uki, which /etc/default/grub names) chainloads that
# release's image. Otherwise — the unified-kernel section absent or malformed,
# the stock entries naming the release, the image's chainloader only in an
# entry GRUB does not start by default — it is refused, naming the section's
# state: "nothing to do" there had the caller report an updated menu that had
# nothing to start the release with by default.
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

# The unified-kernel section's state: "absent" (no markers), "empty" (each marker
# once, in order, and no menu entry between them), "filled", or "malformed"
# (a marker repeated or out of order — such a menu is never composed into).
uki_section_state() {
    awk -v b="$UKI_SECTION_BEGIN" -v e="$UKI_SECTION_END" '
        $0 == b { nb++; if (ne) bad = 1; inside = 1; next }
        $0 == e { ne++; if (!nb) bad = 1; inside = 0; next }
        inside && $0 ~ /^[[:space:]]*menuentry[[:space:]]/ { entries++ }
        END {
            if (!nb && !ne) { print "absent"; exit }
            if (nb != 1 || ne != 1 || bad) { print "malformed"; exit }
            print (entries ? "filled" : "empty")
        }' "$GRUB_CFG"
}

# The kernel-release tokens on the lines outside the unified-kernel section.
outside_section_tokens() {
    awk -v b="$UKI_SECTION_BEGIN" -v e="$UKI_SECTION_END" \
        '$0==b{s=1;next} $0==e{s=0;next} !s' "$GRUB_CFG" | grep -o -e "$TOKEN_RE"
}

ENTRY_LINE="menuentry \"InterGenOS $NEW_KVER (UKI)\" --id intergenos-uki "
CHAIN_LINE="    chainloader /EFI/Linux/intergenos-$NEW_KVER.efi"

# Whether the menu's pinned default entry chainloads this release's boot image,
# read exactly as the installer's own check reads it (bootloader.py
# menu_chainloads_uki): an entry whose menuentry line carries
# --id intergenos-uki (the id /etc/default/grub pins, which the unified-kernel
# generator gives the newest image) and, in its body up to the line that is
# only "}", the line "chainloader /EFI/Linux/intergenos-<release>.efi", blanks
# around it aside. The image's chainloader in any other entry is not that:
# GRUB starts the pinned id by default and, with no entry carrying it, its
# first entry. A menu that only names the release, in its titles or its stock
# entries, does not boot the image either.
menu_chainloads_release() {
    WANT="chainloader /EFI/Linux/intergenos-$NEW_KVER.efi" awk '
        { line = $0; sub(/^[[:space:]]+/, "", line); sub(/[[:space:]]+$/, "", line) }
        body && line == "}" { body = 0 }
        body && line == ENVIRON["WANT"] { found = 1 }
        /^[[:space:]]*menuentry([^[:alnum:]_].*)?[[:space:]]--id[[:space:]]+intergenos-uki([[:space:]]|[{]|$)/ { body = 1 }
        END { exit !found }' "$GRUB_CFG"
}

# Refuse a menu whose release tokens already name this release while its
# pinned default entry does not chainload its boot image. "Nothing to do" there
# told the caller the menu boots this release when nothing GRUB starts by
# default starts the image. Names the state of the unified-kernel section,
# which is why: absent (no section markers), malformed (a marker repeated or
# out of order; such a section is never composed into), or filled with no
# pinned entry for this release's image. Writes nothing.
refuse_no_chainloader() {
    local state why
    state=$(uki_section_state)
    case "$state" in
        absent)    why="its unified-kernel section is absent (no $UKI_SECTION_BEGIN line)" ;;
        malformed) why="its unified-kernel section is malformed (a section marker repeated or out of order), and a malformed section is never composed into" ;;
        *)         why="its unified-kernel section is $state, with no pinned entry for this release's image" ;;
    esac
    _m "ERROR: $GRUB_CFG names $NEW_KVER, but its pinned default entry (--id intergenos-uki) does not chainload /EFI/Linux/intergenos-$NEW_KVER.efi: $why — nothing changed, and the next boot may not start $NEW_KVER"
    exit 1
}

# Fill this system's own unified-kernel section with what its generator emits
# now (see AN EMPTY UNIFIED-KERNEL SECTION, above). Writes the backup first;
# exits non-zero, with the menu restored, on any failure; returns 0 when done.
compose_uki_section() {
    local image="$ESP_UKI_DIR/intergenos-$NEW_KVER.efi"
    local entry_line="$ENTRY_LINE"
    local chain_line="$CHAIN_LINE"
    if [ ! -f "$image" ]; then
        _m "ERROR: $GRUB_CFG has an empty unified-kernel section, and there is no boot image for $NEW_KVER at $image to fill it with — nothing composed"
        exit 1
    fi
    if [ ! -x "$UKI_GENERATOR" ]; then
        _m "ERROR: $GRUB_CFG has an empty unified-kernel section, and the unified-kernel menu generator $UKI_GENERATOR is not there to fill it — nothing composed"
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

    # Post-edit verification: the default entry is this release's and its body
    # chainloads its image; everything outside the section is byte for byte
    # what it was.
    if ! grep -qF -- "$entry_line" "$GRUB_CFG" || ! menu_chainloads_release; then
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
    return 0
}

# Point the entries OUTSIDE the unified-kernel section at this release, after
# the section was just composed (its entries are the generator's, each naming
# its own image, and are left exactly as composed). Restores from $BAK and
# exits non-zero on any failure or verification miss.
repoint_outside_section() {
    local composed="$GRUB_CFG.composed-section.$$" new_file="$GRUB_CFG.repointed.$$"
    _section_of() { awk -v b="$UKI_SECTION_BEGIN" -v e="$UKI_SECTION_END" '$0==b{s=1;next} $0==e{s=0;next} s' "$1"; }
    if ! _section_of "$GRUB_CFG" > "$composed" ||
       ! awk -v b="$UKI_SECTION_BEGIN" -v e="$UKI_SECTION_END" -v kv="$NEW_KVER" '
            $0 == b { s = 1; print; next }
            $0 == e { s = 0; print; next }
            !s { gsub(/[0-9][0-9a-zA-Z.]*-igos-[0-9]+/, kv) }
            { print }' "$GRUB_CFG" > "$new_file" ||
       ! cp "$new_file" "$GRUB_CFG"; then
        rm -f "$composed" "$new_file"
        _m "ERROR: pointing the other entries at $NEW_KVER failed — restoring from $BAK"
        cp -p "$BAK" "$GRUB_CFG"
        exit 1
    fi
    rm -f "$new_file"
    if outside_section_tokens | grep -qv -F "$NEW_KVER" ||
       ! cmp -s "$composed" <(_section_of "$GRUB_CFG") ||
       ! grep -qF -- "$ENTRY_LINE" "$GRUB_CFG" || ! menu_chainloads_release; then
        rm -f "$composed"
        _m "ERROR: post-edit menu failed its checks (a stale release outside the section, the section changed, or no default entry) — restoring from $BAK"
        cp -p "$BAK" "$GRUB_CFG"
        exit 1
    fi
    rm -f "$composed"
    return 0
}

if [ "$(uki_section_state)" = empty ]; then
    compose_uki_section
    if ! outside_section_tokens | grep -qv -F "$NEW_KVER"; then
        _m "menu composed: its unified-kernel section was empty, and the menu now boots $NEW_KVER by default (backup at $BAK)"
        exit 0
    fi
    repoint_outside_section
    _m "menu composed: its unified-kernel section was empty, and the menu now boots $NEW_KVER by default; its other entries now reference $NEW_KVER (backup at $BAK)"
    exit 0
fi

if ! grep -q -- "-igos-" "$GRUB_CFG"; then
    _m "ERROR: $GRUB_CFG contains no InterGenOS kernel-release tokens — refusing to edit an unrecognized menu"
    exit 1
fi

# Idempotence fast-path: nothing to change, when every release token already
# names this release AND the pinned default entry chainloads its image. The
# tokens alone said "nothing to do" for a menu with no unified-kernel entry at
# all, and a chainloader anywhere for one whose only such entry was not the
# default.
if ! grep -e "$TOKEN_RE" "$GRUB_CFG" | grep -qv -F "$NEW_KVER"; then
    menu_chainloads_release || refuse_no_chainloader
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

# Post-edit verification: the pinned default entry must now chainload the new
# release's image (the image's name anywhere else in the menu, a chainloader in
# another entry included, is not that), and no other release token may remain.
if ! menu_chainloads_release; then
    _m "ERROR: post-edit menu's pinned default entry (--id intergenos-uki) does not chainload /EFI/Linux/intergenos-$NEW_KVER.efi — restoring from $BAK"
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
