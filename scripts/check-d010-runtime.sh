#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# scripts/check-d010-runtime.sh — D-010 runtime compliance gate.
#
# S-D 4 (USA-1 audit): companion to scripts/check-d010-compliance.sh —
# the source-grep gate verifies no source path enables intergen.service
# by default; this runtime gate verifies the assembled chroot has no
# preset-enable symlink, no xdg autostart entry, no systemd preset rule,
# and no /etc/skel template that would auto-enable the AI assistant for
# a freshly installed user.
#
# A code change could pass the source-grep gate yet produce a chroot
# with (e.g.) a stray symlink at /etc/systemd/user/default.target.wants/
# intergen.service because some unrelated package's post_install ran
# `systemctl --global preset intergen.service` and the systemd
# preset rules happen to enable it. The runtime gate is the second
# line of defense.
#
# Auto-pass: a build may omit InterGen entirely. If
# /usr/lib/python3.14/site-packages/intergen/ is absent from the chroot,
# the InterGen package was not installed in this build and the gate
# auto-passes (nothing to auto-enable).
#
# Word-bounded matching: D-010 protects the InterGen AI assistant only
# (package name `intergen`, unit `intergen.service`), NOT every package
# whose name starts with `intergen-` (intergen-welcome / intergen-firstboot
# / intergen-pkm-notifier / intergen-toggle / intergen-no-overview). The
# checks look for the literal unit name `intergen.service` and the literal
# executable name `intergen` (no hyphen suffix) — same discipline the
# source-grep gate carries at its Gate A.
#
# Run during phase_squashfs alongside D-007 + D-008 runtime gates.
#
# Usage:
#   scripts/check-d010-runtime.sh <chroot-root>
#
# Exit codes:
#   0 — no violations found; squashfs assembly may proceed
#   1 — one or more violations found; refuse to assemble shippable artifact
#   2 — script invocation error
#
# THE REQUIREMENT (D-010, decided 2026-05-19): the InterGen AI assistant is
# never enabled by default, so the assembled chroot carries no preset-enable
# symlink, no package-distributed target.wants symlink, no xdg autostart
# entry, no systemd preset rule and no /etc/skel template that would start
# intergen.service for a freshly installed user. The installer prompt, which
# defaults to NO, is the sole opt-in surface.
# Source-tree half of the same requirement: scripts/check-d010-compliance.sh

set -uo pipefail

CHROOT_ROOT="${1:-}"
[ -n "$CHROOT_ROOT" ] || { echo "FATAL: chroot-root argument required (e.g. /mnt/igos)" >&2; exit 2; }
[ -d "$CHROOT_ROOT" ] || { echo "FATAL: chroot-root does not exist: $CHROOT_ROOT" >&2; exit 2; }
CHROOT_ROOT="$(cd "$CHROOT_ROOT" && pwd)"

declare -i VIOLATIONS=0

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
header() { printf '\n=== %s ===\n' "$*"; }

violation() {
    red "VIOLATION: $1"
    [ -n "${2:-}" ] && printf '  %s\n' "$2"
    VIOLATIONS=$((VIOLATIONS + 1))
}

echo "D-010 runtime gate"
echo "  chroot: $CHROOT_ROOT"

# Auto-pass — InterGen not installed in this chroot.
INTERGEN_PYROOT="$CHROOT_ROOT/usr/lib/python3.14/site-packages/intergen"
if [ ! -d "$INTERGEN_PYROOT" ]; then
    green "InterGen not installed in chroot — D-010 runtime gate auto-passes (nothing to auto-enable)."
    exit 0
fi

# Gate A — /etc/systemd/user/ and /etc/systemd/system/ have no preset-enable
# symlinks for intergen.service in any *.target.wants/ directory.
#
# These are the admin-side preset symlinks; if a preset operation ran
# during chroot assembly and the preset rules enabled intergen, the
# resulting symlink would persist into the shipped artifact.
header "Gate A — no preset-enable symlinks under /etc/systemd/{user,system}/*.target.wants/"
declare -i GATE_A_VIOLATIONS=0
for d in "$CHROOT_ROOT/etc/systemd/user" "$CHROOT_ROOT/etc/systemd/system"; do
    if [ -d "$d" ]; then
        # Match the literal unit name intergen.service (no hyphen-suffix
        # siblings). find -name on the basename gives exact-match semantics.
        HITS=$(find "$d" -path "*.target.wants/intergen.service" 2>/dev/null)
        if [ -n "$HITS" ]; then
            violation "intergen.service preset-enabled under ${d#$CHROOT_ROOT}" \
                      "D-010 forbids default-enable. Forge wires the symlink at install time on YES path; the shipped chroot must not contain it."
            echo "$HITS" | sed "s|^$CHROOT_ROOT||" | sed 's/^/    /'
            GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
        fi
    fi
done
if [ "$GATE_A_VIOLATIONS" -eq 0 ]; then
    green "PASS — no preset-enable symlinks under /etc/systemd/{user,system}/*.target.wants/"
fi

# Gate B — /usr/lib/systemd/user/ and /usr/lib/systemd/system/ have no
# *.target.wants/intergen.service symlinks distributed by any package.
#
# A package shipping its own pre-baked symlink in /usr/lib/systemd/<scope>/
# default.target.wants/intergen.service would auto-enable intergen the
# moment the unit search path activated — bypassing both `systemctl
# enable` and the Forge prompt. Bake-time violation.
header "Gate B — no package-distributed *.target.wants/intergen.service symlinks under /usr/lib/systemd/"
declare -i GATE_B_VIOLATIONS=0
for d in "$CHROOT_ROOT/usr/lib/systemd/user" "$CHROOT_ROOT/usr/lib/systemd/system"; do
    if [ -d "$d" ]; then
        HITS=$(find "$d" -path "*.target.wants/intergen.service" 2>/dev/null)
        if [ -n "$HITS" ]; then
            violation "intergen.service preset-enabled under ${d#$CHROOT_ROOT}" \
                      "D-010 forbids package-distributed enable. Remove the symlink from the offending package."
            echo "$HITS" | sed "s|^$CHROOT_ROOT||" | sed 's/^/    /'
            GATE_B_VIOLATIONS=$((GATE_B_VIOLATIONS + 1))
        fi
    fi
done
if [ "$GATE_B_VIOLATIONS" -eq 0 ]; then
    green "PASS — no package-distributed *.target.wants/intergen.service symlinks"
fi

# Gate C — no xdg autostart desktop file launching intergen.
#
# /etc/xdg/autostart/*.desktop runs at session start for every user. An
# intergen autostart entry there would be a back-door auto-enable that
# the systemd-side gates miss entirely. Pattern matches the literal
# executable name `intergen` (word-bounded by space-or-EOL) — NOT
# intergen-welcome / intergen-firstboot / etc. The intergen-welcome
# autostart is legitimate (first-login wizard); intergen autostart is
# the D-010 violation.
header "Gate C — no xdg autostart desktop launches intergen"
declare -i GATE_C_VIOLATIONS=0
AUTOSTART_DIR="$CHROOT_ROOT/etc/xdg/autostart"
if [ -d "$AUTOSTART_DIR" ]; then
    for f in "$AUTOSTART_DIR"/*.desktop; do
        [ -f "$f" ] || continue
        if grep -qE '^Exec=(/usr/(local/)?bin/)?intergen([[:space:]]|$)' "$f" 2>/dev/null; then
            # If Hidden=true is present, the entry is suppressed.
            if ! grep -q "^Hidden=true" "$f" 2>/dev/null; then
                violation "xdg autostart entry launches intergen" \
                          "${f#$CHROOT_ROOT} — D-010 forbids back-door auto-launch."
                GATE_C_VIOLATIONS=$((GATE_C_VIOLATIONS + 1))
            fi
        fi
    done
fi
if [ "$GATE_C_VIOLATIONS" -eq 0 ]; then
    green "PASS — no xdg autostart desktop launches intergen"
fi

# Gate D — systemd preset rules don't enable intergen.service.
#
# /usr/lib/systemd/{user,system}-preset/*.preset and the /etc/ overrides
# are processed by `systemctl preset`. A line `enable intergen.service`
# anywhere in these files would auto-enable on every preset run. The
# comment-line filter mirrors the source-grep gate.
header "Gate D — systemd preset rules don't enable intergen.service"
declare -i GATE_D_VIOLATIONS=0
for d in \
    "$CHROOT_ROOT/usr/lib/systemd/user-preset" \
    "$CHROOT_ROOT/usr/lib/systemd/system-preset" \
    "$CHROOT_ROOT/etc/systemd/user-preset" \
    "$CHROOT_ROOT/etc/systemd/system-preset"; do
    if [ -d "$d" ]; then
        # Active (non-comment) `enable intergen.service` lines only.
        HITS=$(grep -rn -E '^[[:space:]]*enable[[:space:]]+intergen\.service([[:space:]]|$)' "$d" 2>/dev/null \
               | grep -v -E '^[^:]+:[0-9]+:[[:space:]]*#')
        if [ -n "$HITS" ]; then
            violation "systemd preset rule enables intergen.service in ${d#$CHROOT_ROOT}" \
                      "D-010 forbids preset-driven auto-enable. Either remove the enable line or change to \`disable intergen.service\`."
            printf '%s\n' "$HITS" | sed "s|^$CHROOT_ROOT||" | sed 's/^/    /'
            GATE_D_VIOLATIONS=$((GATE_D_VIOLATIONS + 1))
        fi
    fi
done
if [ "$GATE_D_VIOLATIONS" -eq 0 ]; then
    green "PASS — no systemd preset rules enable intergen.service"
fi

# Gate E — /etc/skel/ has no template that would auto-enable intergen for
# new users on first login.
#
# /etc/skel/ is copied to a new user's $HOME at useradd time. A template
# at /etc/skel/.config/systemd/user/default.target.wants/intergen.service
# would auto-enable intergen for every new user the moment they log in —
# bypassing Forge's opt-in prompt entirely. Same for /etc/skel/.config/
# autostart/intergen*.desktop and /etc/skel/.config/systemd/user/intergen.
# service-d/ override files that re-enable.
header "Gate E — /etc/skel/ has no auto-enable template for intergen"
declare -i GATE_E_VIOLATIONS=0
SKEL="$CHROOT_ROOT/etc/skel"
if [ -d "$SKEL" ]; then
    SKEL_WANTS=$(find "$SKEL" -path "*.target.wants/intergen.service" 2>/dev/null)
    if [ -n "$SKEL_WANTS" ]; then
        violation "/etc/skel/ contains intergen.service preset-enable template" \
                  "D-010 forbids per-new-user auto-enable. Remove the template."
        echo "$SKEL_WANTS" | sed "s|^$CHROOT_ROOT||" | sed 's/^/    /'
        GATE_E_VIOLATIONS=$((GATE_E_VIOLATIONS + 1))
    fi
    # Autostart templates: only flag entries whose Exec= line launches the
    # literal `intergen` executable (no hyphen suffix).
    SKEL_AUTOSTART_DIR="$SKEL/.config/autostart"
    if [ -d "$SKEL_AUTOSTART_DIR" ]; then
        for f in "$SKEL_AUTOSTART_DIR"/*.desktop; do
            [ -f "$f" ] || continue
            if grep -qE '^Exec=(/usr/(local/)?bin/)?intergen([[:space:]]|$)' "$f" 2>/dev/null; then
                if ! grep -q "^Hidden=true" "$f" 2>/dev/null; then
                    violation "/etc/skel autostart template launches intergen" \
                              "${f#$CHROOT_ROOT} — D-010 forbids per-new-user auto-launch."
                    GATE_E_VIOLATIONS=$((GATE_E_VIOLATIONS + 1))
                fi
            fi
        done
    fi
fi
if [ "$GATE_E_VIOLATIONS" -eq 0 ]; then
    green "PASS — no /etc/skel/ auto-enable template for intergen"
fi

# Summary.
header "D-010 runtime compliance summary"
if [ "$VIOLATIONS" -eq 0 ]; then
    green "ALL GATES PASS — D-010 runtime verified against $CHROOT_ROOT. Squashfs assembly may proceed."
    exit 0
else
    red "FAILED — $VIOLATIONS violation(s) found in built chroot at $CHROOT_ROOT."
    yellow "The requirement: the InterGen AI assistant is never enabled by default — the"
    yellow "chroot carries no preset-enable symlink, target.wants symlink, xdg autostart"
    yellow "entry, preset rule or /etc/skel template that would start intergen.service."
    yellow "The installer prompt, which defaults to NO, is the sole opt-in surface."
    yellow "Fix violations in the build pipeline and re-assemble the chroot."
    exit 1
fi
