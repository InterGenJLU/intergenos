#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# scripts/check-d007-compliance.sh — D-007 compliance gate.
#
# THE REQUIREMENT (D-007, decided 2026-05-18): the shipped system leaves
# remote access and credentials locked down. Root may not log in over SSH,
# host keys are generated at first boot instead of baked into the image, no
# authorized_keys file is baked into a shipped artifact, no password literal
# is hardcoded into account setup, no tty autologs in as root, and the live
# ISO ships the sudo-capable `intergenos` account rather than a root shell.
# This is a Class A gate — it blocks ISO/qcow2 creation until that posture
# holds. This script greps the tree for known violation patterns; any hit
# fails the build.
#
# WHERE THE REQUIREMENT IS RECORDED IN THIS REPOSITORY:
#   packages/core/openssh/files/etc/ssh/sshd_config.d/00-intergenos-d007.conf
#       the shipped sshd drop-in that sets the posture, with the reasoning
#       for each directive in its own comments;
#   packages/core/openssh/build.sh
#       installs the drop-ins and asserts them at build time;
#   packages/core/shadow/build.sh
#       the locked-root /etc/shadow handling;
#   installer/init/init.sh
#       the live-ISO `intergenos` account setup this gate checks for;
#   scripts/check-d007-runtime.sh
#       the runtime half, which checks the assembled chroot rather than
#       the source tree.
#
# Run before `phase_image` (and any ISO-assembly path) in
# scripts/build-intergenos.sh. May also be invoked standalone:
#   scripts/check-d007-compliance.sh
#
# Exit codes:
#   0 — no violations found; build may proceed
#   1 — one or more violations found; refuse to assemble shippable artifact
#   2 — script invocation error (wrong cwd, missing tooling, etc.)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

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

# Gate A: no `ssh-keygen -A` outside a first-boot service unit
header "Gate A — no build-time ssh-keygen -A"
HITS_A=$(grep -rn -E '\bssh-keygen[[:space:]]+-A\b' \
    packages/ scripts/ installer/ 2>/dev/null \
    | grep -v -E 'config/systemd/.*\.service|sshd\.service' \
    | grep -v -E '#.*ssh-keygen' \
    | grep -v -E 'check-d007-compliance\.sh')
if [ -n "$HITS_A" ]; then
    violation "build-time \`ssh-keygen -A\` baked into shipped artifacts" \
              "Host keys MUST be generated at first boot via sshd.service ExecStartPre."
    printf '%s\n' "$HITS_A" | sed 's/^/    /'
else
    green "PASS — no build-time ssh-keygen -A found outside first-boot service units"
fi

# Gate B: sshd_config drop-in or equivalent enforces PermitRootLogin no
header "Gate B — explicit PermitRootLogin no enforced"
if grep -rqE '^PermitRootLogin[[:space:]]+no\b' packages/core/openssh/ 2>/dev/null; then
    green "PASS — PermitRootLogin no shipped explicitly (via openssh build.sh drop-in)"
else
    violation "no explicit PermitRootLogin no in core/openssh tree" \
              "Ship a drop-in at /etc/ssh/sshd_config.d/00-intergenos-d007.conf containing 'PermitRootLogin no'."
fi
# Also flag any explicit PermitRootLogin yes anywhere
HITS_B2=$(grep -rn -E '^[[:space:]]*PermitRootLogin[[:space:]]+yes\b|sed.*PermitRootLogin[[:space:]]+yes' \
    packages/ scripts/ installer/ config/ 2>/dev/null \
    | grep -v -E 'check-d007-compliance\.sh')
if [ -n "$HITS_B2" ]; then
    violation "PermitRootLogin yes referenced in tree" \
              "Root SSH is forbidden on every lane per D-007."
    printf '%s\n' "$HITS_B2" | sed 's/^/    /'
fi

# Gate C: no hardcoded password literals in chpasswd / usermod -p invocations.
# Lock sentinels (!, *, !*, !!) are NOT passwords — they are documented
# /etc/shadow values meaning "this account is locked, no valid password
# will ever match." `usermod -p '!' root` is the canonical way to set
# /etc/shadow's password field to '!' independent of pwconv's behavior
# (see packages/core/shadow/build.sh comment block for the full rationale
# tied to scripts/chroot-build.sh:80's `root:x:` /etc/passwd initialization
# inheriting `x` into pwconv-created /etc/shadow). Excluded below.
header "Gate C — no hardcoded password literals in chpasswd / usermod -p"
HITS_C=$(grep -rn -E 'echo[[:space:]]+["'"'"'][^"'"'"']*:[^"'"'"' $][^"'"'"']*["'"'"'][[:space:]]*\|[[:space:]]*chpasswd|usermod[[:space:]]+.*-p[[:space:]]+["'"'"']' \
    packages/ scripts/ installer/ 2>/dev/null \
    | grep -v -E 'check-d007-compliance\.sh|\.md:|/research/|/audit/|/docs/' \
    | grep -v -E '\$\{[A-Z_]+\}|\$\{?[A-Z_]+_PASSWORD\}?|\$[A-Z_]+_PASSWORD' \
    | grep -v -E "usermod[[:space:]]+-p[[:space:]]+['\"](!|\\*|!\\*|!!)['\"]")
if [ -n "$HITS_C" ]; then
    violation "hardcoded password literal in chpasswd/usermod path" \
              "All non-live credentials must come from env vars or installer-user-prompted input."
    printf '%s\n' "$HITS_C" | sed 's/^/    /'
else
    green "PASS — no hardcoded password literals in chpasswd/usermod paths"
fi

# Gate D: no pre-installed authorized_keys files.
#
# D-007's "no baked authorized_keys" provision is about BUILD TIME --
# nothing baked into the shipped ISO/qcow2. D-019's 2026-05-22 amendment
# explicitly permits user-provided-at-INSTALL-TIME authorized_keys via
# the Forge installer's SSH public-key paste flow (audit-row sshd-
# password-auth Option C closure). Three specific installer files
# implement that flow:
#   - installer/backend/users.py (the _install_ssh_authorized_key helper)
#   - installer/frontend/tui.py (the TUI public-key inputbox prompt)
#   - installer/frontend/gui/screens/packages.py (the GUI textarea)
# These are excluded below. The gate continues to forbid authorized_keys
# references everywhere else: packages/, scripts/, config/, and any
# OTHER installer/ file. New code that needs to touch authorized_keys
# outside the three D-019-blessed paths must (a) demonstrate it's not a
# baked-key path, and (b) update D-019 and this gate to add the new
# code's path to the exemption list.
header "Gate D — no pre-installed authorized_keys files"
HITS_D=$(grep -rn -E 'authorized_keys' \
    packages/ scripts/ installer/ config/ 2>/dev/null \
    | grep -v -E 'check-d007-(compliance|runtime)\.sh|\.md:|/research/|/audit/|/docs/' \
    | grep -v -E '^[^:]+:[0-9]+:[[:space:]]*[#/]' \
    | grep -v -E 'apparmor|grep.*authorized_keys|find.*authorized_keys' \
    | grep -v -E '^(installer/backend/users\.py|installer/frontend/tui\.py|installer/frontend/gui/screens/packages\.py):')
if [ -n "$HITS_D" ]; then
    violation "tree contains references to authorized_keys" \
              "D-007 forbids ANY pre-installed SSH authorized_keys on any lane, ever. Review and remove."
    printf '%s\n' "$HITS_D" | sed 's/^/    /'
else
    green "PASS — no pre-installed authorized_keys references"
fi

# Gate E: live-mode intergenos:intergenos credential setup is present in init.sh
header "Gate E — live-mode intergenos:intergenos credential setup present"
if grep -qE "echo 'intergenos:\\\$6\\\$.*' >> /newroot/etc/shadow" installer/init/init.sh 2>/dev/null; then
    green "PASS — live shadow entry for intergenos user with SHA-512 crypt hash present"
else
    violation "live shadow entry for 'intergenos' user missing in installer/init/init.sh" \
              "D-007 requires the live ISO to ship user intergenos:intergenos sudo-capable."
fi
if grep -qE "^[[:space:]]*echo[[:space:]]+'intergenos:x:1000:1000:" installer/init/init.sh 2>/dev/null; then
    green "PASS — live passwd entry for intergenos user (uid 1000) present"
else
    violation "live passwd entry for 'intergenos' user missing in installer/init/init.sh" \
              "D-007 requires intergenos user as the live-ISO sudo-capable account."
fi

# Gate F: no tty root-autologin anywhere
header "Gate F — no tty root-autologin"
# The `--` before the pattern is load-bearing, not decoration. The pattern begins
# with two dashes, so without the separator grep reads it as an OPTION, fails with
# "unrecognized option", and exits 2 before opening a single file. The 2>/dev/null
# then discarded that error, HITS_F came back empty, and this gate printed PASS on
# every run — for the whole time it existed, a tty root-autologin could not be
# detected here at all. Measured on both matchers available at the time (GNU grep
# 3.12 and ugrep 7.5.0, exit 2 from each) and covered by
# tests/preflight/test_d007_gate_f_detects_autologin.py, which plants the violation
# and requires this gate to refuse it.
HITS_F=$(grep -rn -E -- '--autologin[[:space:]]+root\b' \
    packages/ scripts/ installer/ config/ 2>/dev/null \
    | grep -v -E 'check-d007-compliance\.sh|\.md:|/research/|/audit/|/docs/')
if [ -n "$HITS_F" ]; then
    violation "tty root-autologin configuration in tree" \
              "D-007 forbids root-autologin on any tty. Use intergenos user (live) or user-chosen account (installed)."
    printf '%s\n' "$HITS_F" | sed 's/^/    /'
else
    green "PASS — no tty root-autologin configuration"
fi

# Summary
header "D-007 compliance summary"
if [ "$VIOLATIONS" -eq 0 ]; then
    green "ALL GATES PASS — D-007 compliance verified. Build may proceed."
    exit 0
else
    red "FAILED — $VIOLATIONS violation(s) found."
    yellow "The requirement: root may not log in over SSH, host keys are generated at"
    yellow "first boot rather than baked into the image, no authorized_keys file ships in"
    yellow "a build artifact, no password literal is hardcoded into account setup, and no"
    yellow "tty autologs in as root."
    yellow "The shipped sshd posture is"
    yellow "packages/core/openssh/files/etc/ssh/sshd_config.d/00-intergenos-d007.conf;"
    yellow "each directive's reasoning is in that file's own comments."
    yellow "Fix violations and re-run before ISO/qcow2 assembly may proceed."
    exit 1
fi
