#!/bin/bash
# scripts/check-d007-compliance.sh — D-007 compliance gate.
#
# D-007 (owner directive 2026-05-18, docs/owner-directives.md) is a Class A
# gate that blocks ISO/qcow2 creation until SSH/credentials posture is
# correct. This script greps the tree for known violation patterns; any hit
# fails the build.
#
# Run before `phase_image` (and any ISO-assembly path) in
# scripts/build-intergenos.sh. May also be invoked standalone:
#   scripts/check-d007-compliance.sh
#
# Exit codes:
#   0 — no violations found; build may proceed
#   1 — one or more violations found; refuse to assemble shippable artifact
#   2 — script invocation error (wrong cwd, missing tooling, etc.)
#
# Source-of-truth: docs/owner-directives.md D-007

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

# Gate C: no hardcoded password literals in chpasswd / usermod -p invocations
header "Gate C — no hardcoded password literals in chpasswd / usermod -p"
HITS_C=$(grep -rn -E 'echo[[:space:]]+["'"'"'][^"'"'"']*:[^"'"'"' $][^"'"'"']*["'"'"'][[:space:]]*\|[[:space:]]*chpasswd|usermod[[:space:]]+.*-p[[:space:]]+["'"'"']' \
    packages/ scripts/ installer/ 2>/dev/null \
    | grep -v -E 'check-d007-compliance\.sh|\.md:|/research/|/audit/|/docs/' \
    | grep -v -E '\$\{[A-Z_]+\}|\$\{?[A-Z_]+_PASSWORD\}?|\$[A-Z_]+_PASSWORD')
if [ -n "$HITS_C" ]; then
    violation "hardcoded password literal in chpasswd/usermod path" \
              "All non-live credentials must come from env vars or installer-user-prompted input."
    printf '%s\n' "$HITS_C" | sed 's/^/    /'
else
    green "PASS — no hardcoded password literals in chpasswd/usermod paths"
fi

# Gate D: no pre-installed authorized_keys files
header "Gate D — no pre-installed authorized_keys files"
HITS_D=$(grep -rn -E 'authorized_keys' \
    packages/ scripts/ installer/ config/ 2>/dev/null \
    | grep -v -E 'check-d007-compliance\.sh|\.md:|/research/|/audit/|/docs/' \
    | grep -v -E '^[^:]+:[0-9]+:[[:space:]]*[#/]' \
    | grep -v -E 'apparmor|grep.*authorized_keys|find.*authorized_keys')
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
HITS_F=$(grep -rn -E '--autologin[[:space:]]+root\b' \
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
    yellow "Source-of-truth: docs/owner-directives.md D-007"
    yellow "Fix violations and re-run before ISO/qcow2 assembly may proceed."
    exit 1
fi
