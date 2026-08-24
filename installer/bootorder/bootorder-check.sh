#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# intergenos-bootorder-check — keep the registered boot entry at the front of
# the UEFI boot order.
#
# WHY THIS EXISTS. The installer registers InterGenOS with `efibootmgr
# --create`, which prepends the new entry to BootOrder, and the install trace
# recorded that prepend as done. On a UEFI 2.110 AMI board the installed
# system nevertheless came up with:
#
#     BootOrder: 0001,0000
#     Boot0000* InterGenOS  HD(1,GPT,<esp-guid>,...)/\EFI\InterGenOS\shimx64.efi
#     Boot0001* UEFI OS     HD(1,GPT,<esp-guid>,...)/\EFI\BOOT\BOOTX64.EFI
#
# The firmware had enumerated the removable-media fallback loader on the same
# ESP as its own "UEFI OS" entry and placed it first. Both entries reach the
# same partition, so the machine boots — but through the fallback loader, and
# on a machine with more than one operating system that difference selects a
# different loader than the one the install registered.
#
# Nothing in the tree can stop a firmware from writing its own boot options.
# What the system CAN do is notice and put its own entry back, at every boot,
# and say plainly in the journal what it found and what it changed.
#
# USER CONTROL. The repair happens only when the install recorded that
# InterGenOS is meant to be the default boot target
# (/etc/intergenos/boot-default.conf, written by the installer from the user's
# answer). If the user kept another operating system as the default, this tool
# reports the order and changes nothing.
#
# HONEST REPORTING. A check that cannot run reports that it could not
# determine the state — never a pass and never a failure. The only non-zero
# exit is a repair that was attempted and did not take.

set -uo pipefail

PROGRAM_NAME="intergenos-bootorder-check"
INTENT_FILE="/etc/intergenos/boot-default.conf"
# efibootmgr is installed in an sbin directory — packages/core/efibootmgr
# declares /usr/sbin/efibootmgr in its verify_paths, and that is where it
# lands. Resolve it from a candidate list instead of naming one directory,
# so a packaging or path change cannot leave this check unable to read NVRAM
# while it still exits 0. Naming a single wrong directory is exactly what
# made this guard print "cannot determine boot order" on every boot of an
# install whose firmware HAD demoted the registered entry. An explicit
# --efibootmgr always wins over the list.
EFIBOOTMGR=""
EFIBOOTMGR_CANDIDATES="/usr/sbin/efibootmgr /usr/bin/efibootmgr /sbin/efibootmgr /bin/efibootmgr"
EFI_FIRMWARE_DIR="/sys/firmware/efi"
LABEL_DEFAULT="InterGenOS"
DRY_RUN="no"

usage() {
    cat <<EOF
Usage: ${PROGRAM_NAME} [options]

Options:
  --intent-file PATH   read the default-boot-target record from PATH
                       (default ${INTENT_FILE})
  --efibootmgr PATH    use this efibootmgr binary (default: the first of
                       ${EFIBOOTMGR_CANDIDATES} that is executable)
  --efi-dir PATH       treat PATH as the EFI firmware directory whose
                       presence marks an EFI system (default ${EFI_FIRMWARE_DIR})
  --label LABEL        boot-entry label to keep first (default from the
                       intent file, else ${LABEL_DEFAULT})
  --dry-run            report what would change; write nothing to NVRAM
  -h, --help           this text

Exit codes:
  0  order is correct, was repaired, or the state could not be determined
  1  a repair was attempted and the order still is not correct
  2  usage error
EOF
}

log() { printf '%s: %s\n' "$PROGRAM_NAME" "$*"; }

LABEL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --intent-file) INTENT_FILE="${2:-}"; shift 2 || exit 2 ;;
        --efibootmgr)  EFIBOOTMGR="${2:-}"; shift 2 || exit 2 ;;
        --efi-dir)     EFI_FIRMWARE_DIR="${2:-}"; shift 2 || exit 2 ;;
        --label)       LABEL="${2:-}"; shift 2 || exit 2 ;;
        --dry-run)     DRY_RUN="yes"; shift ;;
        -h|--help)     usage; exit 0 ;;
        *)             log "unknown argument: $1"; usage; exit 2 ;;
    esac
done

# ---- Can this check run at all? -------------------------------------------
if [ ! -d "$EFI_FIRMWARE_DIR" ]; then
    log "not an EFI system ($EFI_FIRMWARE_DIR absent) — nothing to check."
    exit 0
fi
if [ -z "$EFIBOOTMGR" ]; then
    for candidate in $EFIBOOTMGR_CANDIDATES; do
        if [ -x "$candidate" ]; then EFIBOOTMGR="$candidate"; break; fi
    done
fi
if [ -z "$EFIBOOTMGR" ]; then
    log "cannot determine boot order: no executable efibootmgr found in" \
        "$EFIBOOTMGR_CANDIDATES."
    exit 0
fi
if [ ! -x "$EFIBOOTMGR" ]; then
    log "cannot determine boot order: $EFIBOOTMGR is not executable."
    exit 0
fi

# ---- What did the install intend? -----------------------------------------
INTENT=""
INTENT_LABEL=""
if [ -r "$INTENT_FILE" ]; then
    INTENT=$(awk -F= '$1=="default_boot_target" {print $2}' "$INTENT_FILE" \
             | tr -d '[:space:]')
    INTENT_LABEL=$(awk -F= '$1=="boot_entry_label" {print $2}' "$INTENT_FILE" \
                   | tr -d '[:space:]')
else
    log "no recorded install intent at $INTENT_FILE — reporting only."
fi
[ -n "$LABEL" ] || LABEL="${INTENT_LABEL:-$LABEL_DEFAULT}"

# ---- What does the firmware hold? -----------------------------------------
NVRAM=$("$EFIBOOTMGR" 2>&1)
EFIRC=$?
if [ $EFIRC -ne 0 ]; then
    log "cannot determine boot order: $EFIBOOTMGR exited $EFIRC: $NVRAM"
    exit 0
fi

BOOT_ORDER=$(printf '%s\n' "$NVRAM" | awk -F': *' '$1=="BootOrder" {print $2}' \
             | tr -d '[:space:]')
# Entry lines are `Boot<HHHH><*|space> <label><TAB or 2+ spaces><device path>`.
# Match the label exactly, so "InterGenOS" never matches "InterGenOS Recovery".
OURS=$(printf '%s\n' "$NVRAM" | awk -v want="$LABEL" '
    /^Boot[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]/ {
        num = substr($0, 5, 4)
        rest = substr($0, 9)
        sub(/^\*/, "", rest)
        sub(/^[ \t]+/, "", rest)
        # label ends at a tab or at two or more spaces
        if (match(rest, /\t|  +/)) label = substr(rest, 1, RSTART - 1)
        else label = rest
        gsub(/[ \t]+$/, "", label)
        if (label == want) print num
    }')

if [ -z "$BOOT_ORDER" ]; then
    log "cannot determine boot order: firmware reports no BootOrder variable."
    exit 0
fi
if [ -z "$OURS" ]; then
    log "no boot entry labelled '$LABEL' is registered. BootOrder is" \
        "$BOOT_ORDER. This system is booting through some other entry —" \
        "re-register with: efibootmgr --create --disk <disk> --part <n>" \
        "--label '$LABEL' --loader '\\EFI\\InterGenOS\\shimx64.efi'"
    exit 0
fi

FIRST="${BOOT_ORDER%%,*}"
OUR_FIRST="no"
for n in $OURS; do
    if [ "$n" = "$FIRST" ]; then OUR_FIRST="yes"; break; fi
done

if [ "$OUR_FIRST" = "yes" ]; then
    log "BootOrder is $BOOT_ORDER; '$LABEL' ($FIRST) is first — no change."
    exit 0
fi

# Not first. Whether that is wrong depends on what the install recorded.
if [ "$INTENT" != "yes" ]; then
    log "BootOrder is $BOOT_ORDER; '$LABEL' is not first (entries: $(echo $OURS | tr ' ' ','))." \
        "The install recorded default_boot_target=${INTENT:-<none>}, so another" \
        "operating system is meant to boot first — reporting only, nothing changed."
    exit 0
fi

# Intent says we are the default: rebuild the order with our entries first,
# every other entry kept in its existing relative order.
NEW_ORDER=""
for n in $OURS; do
    case ",$BOOT_ORDER," in
        *",$n,"*) NEW_ORDER="${NEW_ORDER:+$NEW_ORDER,}$n" ;;
    esac
done
# An entry that exists but is absent from BootOrder is never considered by the
# firmware — add it, otherwise the repair would silently do nothing.
for n in $OURS; do
    case ",$NEW_ORDER," in
        *",$n,"*) ;;
        *) NEW_ORDER="${NEW_ORDER:+$NEW_ORDER,}$n" ;;
    esac
done
OLD_IFS="$IFS"; IFS=','
for n in $BOOT_ORDER; do
    case ",$NEW_ORDER," in
        *",$n,"*) ;;
        *) NEW_ORDER="${NEW_ORDER:+$NEW_ORDER,}$n" ;;
    esac
done
IFS="$OLD_IFS"

log "BootOrder is $BOOT_ORDER but the install recorded '$LABEL' as the" \
    "default boot target — the firmware moved it. Restoring: $NEW_ORDER"

if [ "$DRY_RUN" = "yes" ]; then
    log "dry run — NVRAM not written."
    exit 0
fi

if ! OUT=$("$EFIBOOTMGR" -o "$NEW_ORDER" 2>&1); then
    log "writing BootOrder failed: $OUT"
    exit 1
fi

# Read back: the write returning 0 is not proof the firmware kept it.
VERIFY=$("$EFIBOOTMGR" 2>&1)
if [ $? -ne 0 ]; then
    log "wrote BootOrder $NEW_ORDER but could not read NVRAM back to confirm."
    exit 1
fi
NOW=$(printf '%s\n' "$VERIFY" | awk -F': *' '$1=="BootOrder" {print $2}' \
      | tr -d '[:space:]')
NOW_FIRST="${NOW%%,*}"
for n in $OURS; do
    if [ "$n" = "$NOW_FIRST" ]; then
        log "BootOrder is now $NOW; '$LABEL' ($NOW_FIRST) is first — repaired."
        exit 0
    fi
done
log "wrote BootOrder $NEW_ORDER but the firmware reports $NOW — '$LABEL' is" \
    "still not first. This firmware is overriding the boot order; select" \
    "InterGenOS in the firmware's own boot-option menu."
exit 1
