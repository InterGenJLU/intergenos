#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# scripts/check-d007-runtime.sh — D-007 runtime compliance gate.
#
# S-D 4 (USA-1 audit; named "most leverage of any item in this report"):
# the existing scripts/check-d007-compliance.sh greps the source tree for
# D-007 violation patterns. This script does the COMPANION check at
# BUILT-ARTIFACT level — it inspects the assembled chroot to confirm the
# policy is actually deployed in the artifact about to be packaged.
#
# The source-grep gate catches "did anyone introduce a violation pattern in
# the source code?" The runtime gate catches "did the build process actually
# deploy a policy-compliant artifact?" — a code change could pass the
# source-grep gate but produce a chroot with (e.g.) a real root password
# hash in /etc/shadow because some package's post_install hook silently
# (or accidentally) wrote one. The runtime gate is the second line of
# defense.
#
# Run during phase_squashfs (after chroot is fully assembled, before
# squashfs assembly) alongside check-license-bundle.sh.
#
# Usage:
#   scripts/check-d007-runtime.sh <chroot-root>
#   scripts/check-d007-runtime.sh /mnt/igos
#
# Exit codes:
#   0 — no violations found; squashfs assembly may proceed
#   1 — one or more violations found; refuse to assemble shippable artifact
#   2 — script invocation error (missing chroot, wrong path, etc.)
#
# THE REQUIREMENT (D-007, decided 2026-05-18; sshd opt-in amended by D-019
# 2026-05-22): the assembled chroot carries no baked SSH host keys, deploys
# `PermitRootLogin no`, carries no pre-installed authorized_keys, leaves root
# locked in /etc/shadow, autologs no tty in as root, and leaves sshd.service
# preset-DISABLED so remote access is something the user turns on. Where it is
# recorded in this repository:
#   packages/core/openssh/files/etc/ssh/sshd_config.d/00-intergenos-d007.conf —
#     the shipped sshd drop-in, with each directive's reasoning in its own
#     comments;
#   packages/core/shadow/build.sh — the locked-root /etc/shadow handling;
#   scripts/check-d007-compliance.sh — the source-tree half of the same
#     requirement.

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

echo "D-007 runtime gate"
echo "  chroot: $CHROOT_ROOT"

# Gate A — No SSH host keys baked into the artifact.
# Host keys MUST be generated at first-boot via sshd.service ExecStartPre.
header "Gate A — no baked SSH host keys at /etc/ssh/"
host_keys=$(find "$CHROOT_ROOT/etc/ssh" \
    -maxdepth 1 -type f \( -name 'ssh_host_*_key' -o -name 'ssh_host_*_key.pub' \) \
    2>/dev/null)
if [ -n "$host_keys" ]; then
    violation "SSH host keys present in /etc/ssh/ of built chroot" \
              "Host keys must be generated at first boot, not baked. Remove from chroot or ensure first-boot service generates them."
    echo "$host_keys" | sed "s|^$CHROOT_ROOT||" | sed 's/^/    /'
else
    green "PASS — no baked SSH host keys"
fi

# Gate B — PermitRootLogin no actually deployed.
header "Gate B — PermitRootLogin no deployed"
sshd_config="$CHROOT_ROOT/etc/ssh/sshd_config"
sshd_dropins="$CHROOT_ROOT/etc/ssh/sshd_config.d"
has_permitroot_no=0
if grep -qE '^[[:space:]]*PermitRootLogin[[:space:]]+no\b' "$sshd_config" 2>/dev/null; then
    has_permitroot_no=1
fi
if [ -d "$sshd_dropins" ]; then
    if grep -qE '^[[:space:]]*PermitRootLogin[[:space:]]+no\b' "$sshd_dropins"/*.conf 2>/dev/null; then
        has_permitroot_no=1
    fi
fi
if [ "$has_permitroot_no" = "1" ]; then
    green "PASS — PermitRootLogin no present in deployed sshd_config or drop-in"
else
    violation "deployed sshd_config does not contain explicit PermitRootLogin no" \
              "Expected at $sshd_config or $sshd_dropins/*.conf."
fi
# Flag any explicit PermitRootLogin yes (forbidden everywhere).
if grep -qrE '^[[:space:]]*PermitRootLogin[[:space:]]+yes\b' "$sshd_config" "$sshd_dropins" 2>/dev/null; then
    violation "PermitRootLogin yes present in deployed sshd config" \
              "Root SSH is forbidden on every lane per D-007."
fi

# Gate C — No pre-installed authorized_keys files anywhere in the chroot.
header "Gate C — no pre-installed authorized_keys"
# Prune /proc /sys /dev /run defensively in case the chroot still has mounts
# present at gate-run time (it should not — chroot-teardown runs during
# phase_image — but defending costs nothing).
authkeys=$(find "$CHROOT_ROOT" \
    \( -path "$CHROOT_ROOT/proc" -o \
       -path "$CHROOT_ROOT/sys" -o \
       -path "$CHROOT_ROOT/dev" -o \
       -path "$CHROOT_ROOT/run" \) -prune -o \
    -type f -name authorized_keys -print \
    2>/dev/null)
if [ -n "$authkeys" ]; then
    violation "authorized_keys files baked into chroot" \
              "D-007 forbids any pre-installed SSH keys."
    echo "$authkeys" | sed "s|^$CHROOT_ROOT||" | sed 's/^/    /'
else
    green "PASS — no pre-installed authorized_keys"
fi

# Gate D — Root is locked in /etc/shadow.
# packages/core/shadow/build.sh:79 (`passwd -l root`) locks root during the
# chroot's core build phase, so by phase_squashfs the shadow file's root
# entry must show a locked sentinel (* or !). Anything that looks like a
# real crypt hash ($1$ / $5$ / $6$ / $y$ etc.) means some downstream step
# silently unlocked root or wrote a hash — D-007 violation.
header "Gate D — root locked in /etc/shadow"
shadow_file="$CHROOT_ROOT/etc/shadow"
if [ ! -f "$shadow_file" ]; then
    violation "$shadow_file missing" "Cannot verify root-lock state."
else
    root_pwd=$(awk -F: '$1=="root"{print $2}' "$shadow_file" | head -1)
    case "$root_pwd" in
        '*'|'!'|'!*'|'!!')
            green "PASS — root is locked (password field: $root_pwd)"
            ;;
        '')
            violation "root has empty password in /etc/shadow" \
                      "Empty password means root login with no creds. Lock it (* or !)."
            ;;
        '$'*)
            violation "root has a real password hash in /etc/shadow" \
                      "Built chroot must ship root locked. Hash prefix: ${root_pwd:0:8}..."
            ;;
        *)
            violation "root /etc/shadow password field is unrecognized" \
                      "Got: $root_pwd. Expected '*' or '!' for locked."
            ;;
    esac
fi

# Gate E — No tty root-autologin baked into the artifact.
# init.sh patches the live-ISO tty setup at first boot (not at build time),
# so any autologin.conf baked into /etc/systemd at squashfs time is a real
# bake-time violation.
header "Gate E — no tty root-autologin"
autologin_files=$(find "$CHROOT_ROOT/etc/systemd" -type f -name 'autologin.conf' 2>/dev/null)
root_autologin_files=""
if [ -n "$autologin_files" ]; then
    for f in $autologin_files; do
        if grep -qE 'Autologin=root|--autologin[[:space:]]+root' "$f" 2>/dev/null; then
            root_autologin_files="$root_autologin_files$f"$'\n'
        fi
    done
fi
if [ -n "$root_autologin_files" ]; then
    violation "tty root-autologin configured in built chroot" \
              "D-007 forbids root-autologin on any tty."
    printf '%s' "$root_autologin_files" | sed "s|^$CHROOT_ROOT||" | sed 's/^/    /'
else
    green "PASS — no tty root-autologin"
fi

# Gate F — D-019 amendment: sshd.service must NOT be preset-enabled.
# D-019 (2026-05-22) makes sshd opt-in via Forge install toggle (default
# off). The shipped chroot must not preset-enable sshd.service in
# /etc/systemd/system/multi-user.target.wants/ — Forge wires it on demand.
header "Gate F — D-019 amendment: sshd.service not preset-enabled"
wants_root="$CHROOT_ROOT/etc/systemd/system/multi-user.target.wants"
if [ -L "$wants_root/sshd.service" ] || [ -L "$wants_root/ssh.service" ]; then
    violation "sshd.service preset-enabled in built chroot" \
              "D-019 amendment requires sshd default-OFF; opt-in via Forge install toggle."
else
    green "PASS — sshd.service not preset-enabled"
fi

# Summary.
header "D-007 runtime compliance summary"
if [ "$VIOLATIONS" -eq 0 ]; then
    green "ALL GATES PASS — D-007 runtime verified against $CHROOT_ROOT. Squashfs assembly may proceed."
    exit 0
else
    red "FAILED — $VIOLATIONS violation(s) found in built chroot at $CHROOT_ROOT."
    yellow "The requirement: no baked SSH host keys, PermitRootLogin no deployed, no"
    yellow "pre-installed authorized_keys, root locked in /etc/shadow, no tty root"
    yellow "autologin, and sshd.service left preset-disabled."
    yellow "The shipped sshd posture is"
    yellow "packages/core/openssh/files/etc/ssh/sshd_config.d/00-intergenos-d007.conf."
    yellow "Fix violations in the build pipeline and re-assemble the chroot."
    exit 1
fi
