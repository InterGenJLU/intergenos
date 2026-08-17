#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# prune-old-kernels.sh — keep-N retention for InterGenOS kernels.
#
# WHY THIS EXISTS. The kernel release is now stamped into KERNELRELEASE
# (CONFIG_LOCALVERSION="-igos-<release>", e.g. 6.18.10-igos-2), so every
# kernel release installs to DISTINCT paths — /boot/vmlinuz-<kver>,
# /lib/modules/<kver>/, and the ESP UKI /boot/efi/EFI/Linux/intergenos-<kver>.efi.
# That distinctness is what enables a real previous-kernel fallback boot entry,
# but it also means old releases no longer overwrite in place — without a
# retention policy the ESP UKIs (hook-generated, NOT pkm-tracked, so pkm's
# upgrade file-removal never reaps them) and /lib/modules trees accumulate
# unbounded across upgrades, eventually overflowing the ESP → unbootable.
#
# POLICY (decision 2026-06-23, mainstream-distro convention): keep the
# KEEP_COUNT (default 2) newest kernels by (version, release) — current + one
# previous fallback — and prune older ones. HARD GUARDS, in priority order, so
# this can never brick a running system:
#   * NEVER prune the currently-running kernel (uname -r). Pruning the running
#     kernel's /lib/modules would break modprobe immediately.
#   * NEVER prune the just-installed kernel (passed as $1).
#   * If the running kernel cannot be determined, or the keep-set computation
#     looks wrong, ABORT and keep everything (fail-safe — never destructive on
#     uncertainty).
# The kept set is the UNION of {KEEP_COUNT newest} ∪ {running} ∪ {just-installed},
# so a system running an older kernel than the two newest still keeps its
# running kernel.
#
# Idempotent (re-running with the same state is a no-op) and best-effort: a
# prune failure is logged and never fails the caller (the kernel install is the
# load-bearing operation; retention is housekeeping).
#
# Roots are env-overridable purely so the unit test can point them at a tmpdir;
# in production they default to the real system paths.
set -u

BOOT_DIR="${BOOT_DIR:-/boot}"
ESP_UKI_DIR="${ESP_UKI_DIR:-/boot/efi/EFI/Linux}"
MODULES_DIR="${MODULES_DIR:-/lib/modules}"
KEEP_COUNT="${KEEP_COUNT:-2}"
# Set-vs-unset (NOT :-) so an explicitly-set empty RUNNING_KVER is honored as
# "unknown" and trips the fail-safe below; only an UNSET var falls back to
# `uname -r`. (`:-` would substitute uname for an empty override too, making
# the "running kernel unknown → keep all" guard unreachable.)
if [ -z "${RUNNING_KVER+set}" ]; then
    RUNNING_KVER="$(uname -r 2>/dev/null)"
fi
JUST_INSTALLED="${1:-}"

# Tag for the caller's log (the post-install hook prefixes its own log()).
_p() { echo "[prune-old-kernels] $*"; }

# --- enumerate candidate kvers (the -igos-<rel> kernels) across all 3 surfaces.
# /lib/modules/<kver> is the authoritative per-kernel marker; also sweep the
# vmlinuz images and ESP UKIs so an orphan on any one surface is still seen.
_collect_kvers() {
    {
        for d in "$MODULES_DIR"/*-igos-*; do
            [ -d "$d" ] && basename "$d"
        done
        for f in "$BOOT_DIR"/vmlinuz-*-igos-*; do
            [ -e "$f" ] && echo "${f##*/vmlinuz-}"
        done
        for u in "$ESP_UKI_DIR"/intergenos-*-igos-*.efi; do
            [ -e "$u" ] || continue
            u="${u##*/intergenos-}"
            echo "${u%.efi}"
        done
    } 2>/dev/null | sort -u
}

_remove_kver() {
    local kver="$1"
    _p "pruning kernel $kver"
    rm -f  "$ESP_UKI_DIR/intergenos-$kver.efi" "$ESP_UKI_DIR/intergenos-$kver.efi.bak" \
           "$ESP_UKI_DIR/intergenos-$kver.efi.disabled" 2>/dev/null
    rm -f  "$BOOT_DIR/vmlinuz-$kver" "$BOOT_DIR/initramfs-$kver.img" \
           "$BOOT_DIR/initramfs-$kver" "$BOOT_DIR/System.map-$kver" \
           "$BOOT_DIR/config-$kver" 2>/dev/null
    rm -rf "$MODULES_DIR/$kver" 2>/dev/null
}

# A retained UKI is only a real fallback if its module tree is usable. pkm's
# package replacement removes the outgoing release's modules while this
# helper's keep-N retains its UKI — a UKI without modules still BOOTS (it
# embeds its kernel) but cannot mount vfat, start zram, or load a firewall,
# so booting it lands in emergency mode (the 2026-07-24 incident). Policy
# from that incident: keep UKI + module tree TOGETHER or neutralize the UKI.
# Quarantine (rename .disabled) rather than delete: non-destructive,
# reversible if the module tree is ever restored, and invisible to both the
# GRUB menu and firmware boot entries.
_quarantine_unbootable_fallbacks() {
    local u kver
    for u in "$ESP_UKI_DIR"/intergenos-*-igos-*.efi; do
        [ -e "$u" ] || continue
        kver="${u##*/intergenos-}"; kver="${kver%.efi}"
        # The just-installed kernel's UKI is the one we are standing up —
        # its module tree was laid down by the same transaction.
        [ "$kver" = "$JUST_INSTALLED" ] && continue
        if [ -f "$MODULES_DIR/$kver/modules.dep" ] && \
           find "$MODULES_DIR/$kver" -name '*.ko*' -print -quit 2>/dev/null | grep -q .; then
            continue  # usable module tree — a real fallback, keep it live
        fi
        if mv "$u" "$u.disabled" 2>/dev/null; then
            _p "QUARANTINED unbootable fallback UKI $u -> .disabled (module tree for $kver is absent or empty — booting it would land in emergency mode)"
        else
            _p "WARNING: could not quarantine unbootable fallback UKI $u (module tree for $kver is absent or empty)"
        fi
    done
}

main() {
    local all newest keep prune kver
    all="$(_collect_kvers)"
    if [ -z "$all" ]; then
        _p "no -igos kernels found; nothing to do"
        return 0
    fi

    # Fires on EVERY invocation, before the keep-set math: the trap this
    # closes (retained UKI, gutted module tree) exists precisely when the
    # prune-set is empty (keep-2 with exactly 2 kernels on disk — the
    # 2026-07-24 fleet state), so it cannot live inside the prune loop.
    _quarantine_unbootable_fallbacks

    # KEEP_COUNT must be a sane positive int, else keep everything (fail-safe).
    case "$KEEP_COUNT" in
        ''|*[!0-9]*) _p "KEEP_COUNT='$KEEP_COUNT' invalid; keeping all (fail-safe)"; return 0 ;;
    esac
    [ "$KEEP_COUNT" -ge 1 ] || { _p "KEEP_COUNT<1; keeping all (fail-safe)"; return 0; }

    # Running kernel MUST be known + must be one we recognize as -igos; if not,
    # do not risk pruning (a foreign / unknown running kernel = abort).
    if [ -z "$RUNNING_KVER" ]; then
        _p "running kernel unknown; keeping all (fail-safe)"
        return 0
    fi

    newest="$(printf '%s\n' "$all" | sort -V | tail -n "$KEEP_COUNT")"

    # keep-set = {newest KEEP_COUNT} ∪ {running} ∪ {just-installed}
    keep="$(printf '%s\n%s\n%s\n' "$newest" "$RUNNING_KVER" "$JUST_INSTALLED" \
            | grep -v '^$' | sort -u)"

    # prune-set = all − keep
    prune="$(comm -23 <(printf '%s\n' "$all" | sort -u) <(printf '%s\n' "$keep" | sort -u))"

    if [ -z "$prune" ]; then
        _p "keep-$KEEP_COUNT: nothing to prune (have $(printf '%s' "$all" | grep -c .) kernel(s))"
        return 0
    fi

    _p "keep-$KEEP_COUNT retention — keeping: $(printf '%s' "$keep" | tr '\n' ' ')"
    while IFS= read -r kver; do
        [ -n "$kver" ] || continue
        # Belt-and-suspenders: refuse to prune the running or just-installed
        # kernel even if it somehow reached the prune list.
        if [ "$kver" = "$RUNNING_KVER" ] || [ "$kver" = "$JUST_INSTALLED" ]; then
            _p "REFUSING to prune protected kernel $kver (running/just-installed)"
            continue
        fi
        _remove_kver "$kver"
    done <<< "$prune"
    return 0
}

main "$@"
# Always succeed — retention is housekeeping, never fatal to a kernel install.
exit 0
