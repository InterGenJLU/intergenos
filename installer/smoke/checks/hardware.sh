#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# installer/smoke/checks/hardware.sh — Category 8: unclaimed hardware.
#
# Confirms the kernel actually CLAIMED the hardware this machine has, and that
# the devices a user touches on day one exist.
#
# WHY THIS CATEGORY EXISTS
# ------------------------
# Every other category can pass on a machine whose hardware is half dead. A
# kernel built without a driver does not fail loudly — the device is simply not
# there, the logs say nothing, every service is healthy, and the defect is found
# by a person noticing that the speakers, the card reader or the touchpad do not
# work. Three such defects were found together on one laptop; none of them
# produced a failed unit, an error log, or a non-zero exit anywhere.
#
# They are also structurally invisible to the evaluation path we already had:
# evaluations run in virtual machines, and a virtual machine has no SD card
# reader, no I2C-HID touchpad and no on-board audio codec to lose. Only a real
# hardware install can exhibit this class, which is exactly why this check runs
# there.
#
# THE PRINCIPLE
# -------------
# Absent hardware is not a defect; a desktop has no battery and no touchpad. The
# defect is hardware that is PRESENT and UNUSABLE. So each check below asks two
# questions in order — is the hardware here, and if so does it work — and only
# the present-but-dead answer is a FAIL. Genuinely absent hardware is SKIP, so
# the result stays readable on every form factor.
#
# THE HIGHEST-SIGNAL CHECK is the deferred-probe list. A device lands there when
# the kernel WANTED to bind a driver and could not, then gave up. It is close to
# false-positive-free: a non-empty list is the kernel itself reporting hardware
# it failed to bring up. On the laptop above it held exactly one line —
# "i2c-ELAN0788:00  i2c_hid_acpi: can't get irq" — the dead touchpad, named,
# with its reason, at boot, where nothing was reading it.

# Devices under these PCI classes are bridges and platform glue that legitimately
# have no driver bound on a healthy system. Listing them as unclaimed is noise.
# Anything OUTSIDE this set that is unclaimed is worth a human's attention.
# Codes are the PCI class+subclass as sysfs reports them (0xCCSSPP -> CCSS).
# These were CORRECTED against a real machine on 2026-08-07: the first draft
# guessed 0605/0680 for the shared-SRAM and system-peripheral entries, and the
# live firing reported both of them as findings because their real codes are
# 0500 and 0880. A class list written from memory is a list that cries wolf.
SMOKE_HW_UNCLAIMED_OK_CLASSES=(
    "0600"  # Host bridge
    "0601"  # ISA bridge
    "0604"  # PCI bridge
    "0500"  # RAM memory (PCH shared SRAM and similar)
    "0880"  # Base system peripheral (neural accelerator, timers and similar)
)

# ---------------------------------------------------------------------------
# Injection points.
#
# Overridable ONLY so this check can be PROVEN against constructed conditions
# before it is trusted to certify a real machine — a check never shown to detect
# a true positive cannot certify a zero, and the conditions it looks for (a dead
# card reader, an audio controller with no card) cannot be created on demand on
# the machine running the tests. The defaults are the real system and are what
# every real run uses; nothing in normal operation sets these.
#
# SMOKE_HW_ROOT prefixes the sysfs/proc/dev lookups, so a test can build a fake
# machine in a temporary directory. The two command hooks let a test supply a
# stub in place of lspci/aplay.
# ---------------------------------------------------------------------------
SMOKE_HW_ROOT="${SMOKE_HW_ROOT:-}"
SMOKE_HW_LSPCI="${SMOKE_HW_LSPCI:-lspci}"
SMOKE_HW_APLAY="${SMOKE_HW_APLAY:-aplay}"
SMOKE_HW_FORCE_VIRT="${SMOKE_HW_FORCE_VIRT:-}"

# Path helper — every filesystem lookup in this file goes through it.
smoke_hw_p() { printf '%s%s' "$SMOKE_HW_ROOT" "$1"; }

# ---------------------------------------------------------------------------
# Is this a virtual machine? Hardware-presence checks below are meaningful only
# on real hardware; in a guest they would report absences that are correct.
# ---------------------------------------------------------------------------
smoke_hw_is_virtual() {
    local virt
    if [ -n "$SMOKE_HW_FORCE_VIRT" ]; then
        [ "$SMOKE_HW_FORCE_VIRT" = "1" ] && return 0
        return 1
    fi
    if command -v systemd-detect-virt >/dev/null 2>&1; then
        virt="$(systemd-detect-virt 2>/dev/null || true)"
        [ -n "$virt" ] && [ "$virt" != "none" ] && return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# hw/deferred — the kernel's own list of devices it failed to bring up.
#
# This is the check that would have caught the dead touchpad on first run.
# ---------------------------------------------------------------------------
check_hardware_deferred_probe() {
    local node; node="$(smoke_hw_p /sys/kernel/debug/devices_deferred)"

    if [ ! -r "$node" ]; then
        # debugfs not mounted, or not running as root. Say which, rather than
        # reporting a clean list we never actually read — an unread list is not
        # an empty one.
        if [ "$(id -u)" -ne 0 ]; then
            check_skip "hw/deferred" "needs root to read $node"
        else
            check_skip "hw/deferred" "$node absent (debugfs not mounted?)"
        fi
        return
    fi

    local count entries
    entries="$(cat "$node" 2>/dev/null)"
    count="$(printf '%s' "$entries" | grep -c . || true)"

    if [ "$count" -eq 0 ]; then
        check_pass "hw/deferred" "no devices left on the deferred-probe list"
        return
    fi

    # Each line is "<device>\t<driver>: <reason>". Report every one — a device
    # the kernel gave up on is a device the user does not have.
    local flat
    flat="$(printf '%s' "$entries" | tr '\t' ' ' | tr '\n' ';' | sed 's/;$//')"
    check_fail "hw/deferred" "$count device(s) the kernel could not bring up: $flat"
}

# ---------------------------------------------------------------------------
# hw/unclaimed-pci — PCI functions with no driver bound.
#
# WARN, not FAIL: some unclaimed functions are normal (see the class list
# above), and which of the rest matter is a judgement a human makes. The value
# is that they are NAMED in the install record instead of going unnoticed.
# ---------------------------------------------------------------------------
check_hardware_unclaimed_pci() {
    if [ ! -d "$(smoke_hw_p /sys/bus/pci/devices)" ]; then
        check_skip "hw/unclaimed-pci" "no PCI bus in this machine"
        return
    fi

    local dev addr class unclaimed=() ok
    for dev in "$(smoke_hw_p /sys/bus/pci/devices)"/*; do
        [ -e "$dev" ] || continue
        [ -e "$dev/driver" ] && continue
        addr="$(basename "$dev")"
        class="$(cat "$dev/class" 2>/dev/null || echo 0x000000)"
        # /sys class is 0xCCSSPP — take the 4 hex digits of class+subclass.
        class="${class#0x}"
        class="${class:0:4}"
        ok=0
        for c in "${SMOKE_HW_UNCLAIMED_OK_CLASSES[@]}"; do
            [ "$class" = "$c" ] && { ok=1; break; }
        done
        [ "$ok" = "1" ] && continue
        unclaimed+=("$addr(class $class)")
    done

    if [ "${#unclaimed[@]}" -eq 0 ]; then
        check_pass "hw/unclaimed-pci" "every PCI function has a driver bound (bridges excepted)"
        return
    fi
    check_warn "hw/unclaimed-pci" "${#unclaimed[@]} unclaimed: ${unclaimed[*]}"
}

# ---------------------------------------------------------------------------
# hw/audio — an audio controller with no usable playback device.
#
# This is the shape the dead speakers took: the PCI audio controller had a
# driver bound and looked healthy, but no sound card was ever registered
# because the machine-specific driver that ties codec to controller was not
# built. "Driver bound" is not "device works".
# ---------------------------------------------------------------------------
check_hardware_audio() {
    local controller cards analog
    controller=0
    if command -v "$SMOKE_HW_LSPCI" >/dev/null 2>&1; then
        controller="$("$SMOKE_HW_LSPCI" 2>/dev/null | grep -ciE 'audio device|audio controller|multimedia audio' || true)"
    fi

    if [ "$controller" -eq 0 ]; then
        check_skip "hw/audio" "no audio controller in this machine"
        return
    fi

    # `grep -c` EXITS 1 when it counts zero, and it has already printed "0" by
    # then — so `|| echo 0` appends a SECOND line and the variable becomes two
    # lines, which breaks the numeric test that follows. Take grep's own count
    # and only substitute a default when the command produced nothing at all.
    cards="$(grep -c '^ *[0-9]' "$(smoke_hw_p /proc/asound/cards)" 2>/dev/null || true)"
    cards="${cards:-0}"
    if [ "$cards" -eq 0 ]; then
        check_fail "hw/audio" "audio controller present but NO sound card registered — speakers cannot work"
        return
    fi

    # A card that is HDMI-only means the machine can play sound through a monitor
    # and not through its own speakers. That is the exact half-working state the
    # laptop was in, and it must not read as healthy.
    analog=0
    if command -v "$SMOKE_HW_APLAY" >/dev/null 2>&1; then
        analog="$("$SMOKE_HW_APLAY" -l 2>/dev/null | grep '^card' | grep -viE 'HDMI|DisplayPort' | grep -c . || true)"
    else
        # No alsa-utils: fall back to the card list, and say the check was weaker.
        check_warn "hw/audio" "$cards card(s) registered; aplay absent so analog-vs-HDMI not distinguished"
        return
    fi

    if [ "$analog" -eq 0 ]; then
        check_fail "hw/audio" "$cards card(s) but NO analog playback device — built-in speakers cannot work"
        return
    fi
    check_pass "hw/audio" "$cards card(s), $analog analog playback device(s)"
}

# ---------------------------------------------------------------------------
# hw/card-reader — a card reader in hardware with no MMC host.
#
# The bridge driver binds and the machine looks fine; no block device can ever
# appear because the whole MMC subsystem was absent from the kernel.
# ---------------------------------------------------------------------------
check_hardware_card_reader() {
    local reader hosts
    reader=0
    if command -v "$SMOKE_HW_LSPCI" >/dev/null 2>&1; then
        reader="$("$SMOKE_HW_LSPCI" 2>/dev/null | grep -ciE 'sd host controller|mmc|card reader|rts5[0-9]' || true)"
    fi

    if [ "$reader" -eq 0 ]; then
        check_skip "hw/card-reader" "no card reader in this machine"
        return
    fi

    hosts=0
    [ -d "$(smoke_hw_p /sys/class/mmc_host)" ] && hosts="$(find "$(smoke_hw_p /sys/class/mmc_host)" -mindepth 1 -maxdepth 1 | grep -c . || true)"

    if [ "$hosts" -eq 0 ]; then
        check_fail "hw/card-reader" "card reader present in hardware but NO MMC host registered — no card can ever mount"
        return
    fi
    check_pass "hw/card-reader" "$hosts MMC host(s) for $reader reader device(s)"
}

# ---------------------------------------------------------------------------
# hw/pointer — a laptop with no pointing device.
#
# Gated on the machine having an internal keyboard, which is what distinguishes
# a laptop from a desktop that legitimately has no touchpad.
# ---------------------------------------------------------------------------
check_hardware_pointer() {
    local devices internal_kb touchpad
    devices="$(smoke_hw_p /proc/bus/input/devices)"
    if [ ! -r "$devices" ]; then
        check_skip "hw/pointer" "$devices not readable"
        return
    fi

    internal_kb="$(grep -ci 'AT Translated Set 2 keyboard' "$devices" || true)"
    touchpad="$(grep -ciE 'touchpad|trackpad|synaptics|elan.*(mouse|touch)' "$devices" || true)"

    if [ "$touchpad" -gt 0 ]; then
        check_pass "hw/pointer" "touchpad present"
        return
    fi
    if [ "$internal_kb" -eq 0 ]; then
        check_skip "hw/pointer" "no internal keyboard — not a laptop, touchpad not expected"
        return
    fi
    check_fail "hw/pointer" "internal keyboard present but NO touchpad — a laptop with no pointing device"
}

# ---------------------------------------------------------------------------
# hw/day-one — the remaining things a user touches on the first boot.
#
# Each is present-or-absent only; a missing one is reported as a single WARN
# naming everything missing, because on some form factors any individual one is
# legitimately absent and a per-item FAIL would cry wolf.
# ---------------------------------------------------------------------------
check_hardware_day_one() {
    local missing=() present=()

    # Battery.
    if find "$(smoke_hw_p /sys/class/power_supply)" -maxdepth 1 -name 'BAT*' 2>/dev/null | grep -q .; then
        present+=("battery")
    else
        missing+=("battery")
    fi

    # Screen brightness.
    if [ -d "$(smoke_hw_p /sys/class/backlight)" ] && find "$(smoke_hw_p /sys/class/backlight)" -mindepth 1 -maxdepth 1 2>/dev/null | grep -q .; then
        present+=("backlight")
    else
        missing+=("backlight")
    fi

    # Wireless. Count DIRECTORIES per interface, never `ls dir | wc -l` — on an
    # empty directory that returns 0 and reports "no Wi-Fi" on a machine whose
    # radio works. phy80211 is the mac80211 link and is the primary signal.
    local wifi=0 iface
    for iface in "$(smoke_hw_p /sys/class/net)"/*/; do
        [ -e "${iface}phy80211" ] || [ -d "${iface}wireless" ] && wifi=$((wifi + 1))
    done
    if [ "$wifi" -gt 0 ]; then present+=("wifi"); else missing+=("wifi"); fi

    # Bluetooth.
    if [ -d "$(smoke_hw_p /sys/class/bluetooth)" ] && find "$(smoke_hw_p /sys/class/bluetooth)" -mindepth 1 -maxdepth 1 2>/dev/null | grep -q .; then
        present+=("bluetooth")
    else
        missing+=("bluetooth")
    fi

    # USB Type-C port management. Absent means no dock or DisplayPort-alt-mode
    # information is available to userspace.
    if [ -d "$(smoke_hw_p /sys/class/typec)" ] && find "$(smoke_hw_p /sys/class/typec)" -mindepth 1 -maxdepth 1 2>/dev/null | grep -q .; then
        present+=("usb-c")
    else
        missing+=("usb-c")
    fi

    # Camera.
    if find "$(smoke_hw_p /dev)" -maxdepth 1 -name 'video*' 2>/dev/null | grep -q .; then
        present+=("camera")
    else
        missing+=("camera")
    fi

    if [ "${#missing[@]}" -eq 0 ]; then
        check_pass "hw/day-one" "all present: ${present[*]}"
        return
    fi
    check_warn "hw/day-one" "absent: ${missing[*]} | present: ${present[*]:-none}"
}

# ---------------------------------------------------------------------------
# Category entry point.
# ---------------------------------------------------------------------------
run_hardware_checks() {
    # The deferred-probe list is meaningful everywhere, virtual machines
    # included — a guest device the kernel could not bind is still a defect.
    check_hardware_deferred_probe

    if smoke_hw_is_virtual; then
        check_skip "hw/physical" "virtual machine — physical-hardware checks not applicable"
        return
    fi

    check_hardware_unclaimed_pci
    check_hardware_audio
    check_hardware_card_reader
    check_hardware_pointer
    check_hardware_day_one
}
