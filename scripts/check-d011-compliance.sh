#!/bin/bash
# scripts/check-d011-compliance.sh — D-011 compliance gate.
#
# D-011 (owner directive 2026-05-19, docs/owner-directives.md) is a Class A
# gate that blocks ISO/qcow2 creation until the default-deny firewall posture
# is correct. This script greps the tree for known violation patterns; any
# hit fails the build.
#
# Run before `phase_image` (and any ISO-assembly path) in
# scripts/build-intergenos.sh. May also be invoked standalone:
#   scripts/check-d011-compliance.sh
#
# Exit codes:
#   0 — no violations found; build may proceed
#   1 — one or more violations found; refuse to assemble shippable artifact
#   2 — script invocation error (wrong cwd, missing tooling, etc.)
#
# Source-of-truth: docs/owner-directives.md D-011

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

DEFAULTS_CONF="packages/core/intergenos-firewall-defaults/nftables.conf"

# Gate A: input chain policy is `drop` in the canonical SSoT conf
header "Gate A — input chain policy=drop in intergenos-firewall-defaults"
if [ ! -f "$DEFAULTS_CONF" ]; then
    violation "$DEFAULTS_CONF missing" \
              "intergenos-firewall-defaults package must ship the canonical SSoT nftables.conf."
elif grep -qE '^[[:space:]]+type filter hook input priority filter; policy drop;' "$DEFAULTS_CONF"; then
    green "PASS — input chain policy=drop"
else
    violation "input chain policy is not 'drop' in $DEFAULTS_CONF" \
              "D-011 mandates default-deny on the input chain."
fi

# Gate B: forward chain policy is `drop` in the canonical SSoT conf
header "Gate B — forward chain policy=drop"
if [ -f "$DEFAULTS_CONF" ] && grep -qE '^[[:space:]]+type filter hook forward priority filter; policy drop;' "$DEFAULTS_CONF"; then
    green "PASS — forward chain policy=drop"
else
    violation "forward chain policy is not 'drop' in $DEFAULTS_CONF" \
              "D-011 mandates default-deny on the forward chain."
fi

# Gate C: port 22 (SSH) is NOT in the default accept rules
header "Gate C — SSH (tcp/22) closed by default"
if [ -f "$DEFAULTS_CONF" ] && grep -qE 'tcp\s+dport\s+22\s+.*accept' "$DEFAULTS_CONF"; then
    violation "tcp/22 (SSH) accept rule present in $DEFAULTS_CONF" \
              "D-011 mandates SSH closed by default. Remove the accept rule; users open it themselves."
else
    green "PASS — no SSH accept rule in default conf"
fi

# Gate D: install-theming.sh does NOT contain a firewall write block
header "Gate D — install-theming.sh firewall block retired"
HITS_D=$(grep -nE 'cat[[:space:]]+>[[:space:]]+/etc/nftables\.conf|nft -f /etc/nftables\.conf' \
    scripts/install-theming.sh 2>/dev/null \
    | grep -v -E 'check-d011-compliance\.sh|^[^:]+:[0-9]+:[[:space:]]*#')
if [ -n "$HITS_D" ]; then
    violation "install-theming.sh still contains firewall write block" \
              "D-011 retires install-theming.sh's firewall block; intergenos-firewall-defaults is sole writer."
    printf '%s\n' "$HITS_D" | sed 's/^/    /'
else
    green "PASS — install-theming.sh firewall block retired"
fi

# Gate E: upstream packages/core/nftables/ does NOT ship /etc/nftables.conf
header "Gate E — packages/core/nftables/ stays policy-neutral"
if [ -f "packages/core/nftables/nftables.conf" ]; then
    violation "packages/core/nftables/nftables.conf still present" \
              "D-011 requires the upstream-tool package to stay policy-neutral. Delete packages/core/nftables/nftables.conf and the corresponding install line in build.sh."
elif grep -qE '^[[:space:]]+install -Dm644.*nftables\.conf' packages/core/nftables/build.sh 2>/dev/null; then
    violation "packages/core/nftables/build.sh still installs /etc/nftables.conf" \
              "Remove the install line; intergenos-firewall-defaults owns /etc/nftables.conf."
else
    green "PASS — upstream nftables package is policy-neutral"
fi

# Gate F: nftables.service preset enables the service
header "Gate F — nftables.service enabled via preset"
if grep -qE '^enable[[:space:]]+nftables\.service' packages/core/nftables/90-nftables.preset 2>/dev/null; then
    green "PASS — nftables.service enabled by 90-nftables.preset"
else
    violation "nftables.service not enabled by systemd preset" \
              "packages/core/nftables/90-nftables.preset must contain 'enable nftables.service'."
fi

# Summary
header "D-011 compliance summary"
if [ "$VIOLATIONS" -eq 0 ]; then
    green "ALL GATES PASS — D-011 compliance verified. Build may proceed."
    exit 0
else
    red "FAILED — $VIOLATIONS violation(s) found."
    yellow "Source-of-truth: docs/owner-directives.md D-011"
    yellow "Fix violations and re-run before ISO/qcow2 assembly may proceed."
    exit 1
fi
