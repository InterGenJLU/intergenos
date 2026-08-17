#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# scripts/check-d011-runtime.sh — D-011 runtime compliance gate.
#
# S-D 4 (USA-1 audit): companion to scripts/check-d011-compliance.sh —
# the source-grep gate verifies the canonical SSoT nftables.conf in
# the intergenos-firewall-defaults package has the right policy + rule
# set + retired patterns. This runtime gate verifies the chroot at
# /mnt/igos has the policy ACTUALLY DEPLOYED at /etc/nftables.conf
# AND that nftables.service is preset-enabled.
#
# A code change could pass the source-grep gate yet produce a chroot
# whose /etc/nftables.conf was overwritten by some other package's
# post_install (e.g. a third-party firewall manager landing in the
# tree). The runtime gate is the second line of defense.
#
# No auto-pass: D-011 is a Class A baseline-security ship-block. The
# default-deny firewall is core system policy per
# packages/core/intergenos-firewall-defaults/build.sh ("tier=core:
# default firewall posture is core system policy on InterGenOS, not
# optional"). Missing /etc/nftables.conf fails the gate.
#
# Run during phase_squashfs alongside D-007 + D-008 + D-010 runtime gates.
#
# Usage:
#   scripts/check-d011-runtime.sh <chroot-root>
#
# Exit codes:
#   0 — no violations found; squashfs assembly may proceed
#   1 — one or more violations found; refuse to assemble shippable artifact
#   2 — script invocation error
#
# THE REQUIREMENT (D-011, decided 2026-05-19): the shipped firewall runs
# default-deny — input and forward both policy drop, SSH (tcp/22) closed, the
# accept rules basic networking needs present and no others, and the policy
# actually deployed at /etc/nftables.conf with nftables.service preset-enabled.
# Where it is recorded in this repository:
#   packages/core/intergenos-firewall-defaults/nftables.conf — the shipped
#     ruleset, with the reasoning for each rule in its own comments;
#   scripts/lib/d011-accept-list.sh — APPROVED_ACCEPTS, the authorized accept
#     rules, shared with scripts/check-d011-compliance.sh and the pytest half.

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

echo "D-011 runtime gate"
echo "  chroot: $CHROOT_ROOT"

NFTABLES_CONF="$CHROOT_ROOT/etc/nftables.conf"

# The approved accept list, the ruleset reader and the membership predicate
# live in scripts/lib/d011-accept-list.sh — the same record the source gate
# uses, so the two cannot drift apart. Sourcing is FAIL-CLOSED: a gate that
# cannot load its own approved list certifies nothing. This gate is run from
# the build tree, so the library sits beside it.
D011_LIB="$(cd "$(dirname "$0")" && pwd)/lib/d011-accept-list.sh"
if [ ! -f "$D011_LIB" ]; then
    red "FATAL: $D011_LIB missing — cannot evaluate the approved accept list."
    exit 2
fi
# shellcheck source=lib/d011-accept-list.sh
source "$D011_LIB"

# Gate A — /etc/nftables.conf deployed.
header "Gate A — /etc/nftables.conf deployed"
if [ ! -f "$NFTABLES_CONF" ]; then
    violation "/etc/nftables.conf missing in chroot" \
              "intergenos-firewall-defaults must deploy /etc/nftables.conf. Verify the package built + installed."
    # All downstream gates depend on the file existing; fail hard now.
    red "FAILED — cannot evaluate Gates B-L without /etc/nftables.conf."
    yellow "The shipped ruleset that should have been deployed is"
    yellow "packages/core/intergenos-firewall-defaults/nftables.conf."
    exit 1
fi
green "PASS — /etc/nftables.conf deployed"

# Gate B — input chain policy=drop deployed.
header "Gate B — input chain policy=drop"
if grep -qE '^[[:space:]]+type filter hook input priority filter; policy drop;' "$NFTABLES_CONF"; then
    green "PASS — input chain policy=drop"
else
    violation "deployed /etc/nftables.conf does not have input chain policy=drop" \
              "D-011 mandates default-deny on input chain."
fi

# Gate C — forward chain policy=drop deployed.
header "Gate C — forward chain policy=drop"
if grep -qE '^[[:space:]]+type filter hook forward priority filter; policy drop;' "$NFTABLES_CONF"; then
    green "PASS — forward chain policy=drop"
else
    violation "deployed /etc/nftables.conf does not have forward chain policy=drop" \
              "D-011 mandates default-deny on forward chain."
fi

# Gate D — SSH (tcp/22) NOT in deployed accept rules.
header "Gate D — SSH (tcp/22) closed by default"
if grep -qE 'tcp\s+dport\s+22\s+.*accept' "$NFTABLES_CONF"; then
    violation "deployed /etc/nftables.conf has tcp/22 accept rule" \
              "D-011 mandates SSH closed by default. Some downstream rewriter introduced the rule."
else
    green "PASS — no SSH accept rule in deployed conf"
fi

# Gate E — nftables.service preset-enabled in chroot.
# The intergenos-firewall-defaults package's policy is useless if
# nftables.service doesn't activate at boot. The packages/core/nftables/
# 90-nftables.preset file should drive systemctl preset-all to create
# the multi-user.target.wants/ symlink during chroot assembly.
header "Gate E — nftables.service preset-enabled"
NFT_WANTS="$CHROOT_ROOT/etc/systemd/system/multi-user.target.wants/nftables.service"
NFT_UNIT="$CHROOT_ROOT/usr/lib/systemd/system/nftables.service"
if [ ! -f "$NFT_UNIT" ] && [ ! -L "$NFT_UNIT" ]; then
    violation "nftables.service unit file missing in chroot" \
              "Expected $NFT_UNIT (packages/core/nftables installs this)."
elif [ ! -L "$NFT_WANTS" ] && [ ! -f "$NFT_WANTS" ]; then
    violation "nftables.service not preset-enabled in chroot" \
              "Expected $NFT_WANTS symlink. Verify 90-nftables.preset is processed during chroot assembly."
else
    green "PASS — nftables.service unit + preset-enable symlink deployed"
fi

# Gate F — ct state established,related accept deployed.
header "Gate F — ct state established,related accept"
if grep -qE '^[[:space:]]+ct[[:space:]]+state[[:space:]]+established,related[[:space:]]+accept' "$NFTABLES_CONF"; then
    green "PASS — ct state established,related accept present"
else
    violation "ct state established,related accept missing from deployed /etc/nftables.conf" \
              "Without this, every outbound connection one-ways at SYN-ACK."
fi

# Gate G — iif lo accept deployed.
header "Gate G — loopback (iif lo) accept"
if grep -qE '^[[:space:]]+iif[[:space:]]+"?lo"?[[:space:]]+accept' "$NFTABLES_CONF"; then
    green "PASS — iif lo accept present"
else
    violation "iif lo accept missing from deployed /etc/nftables.conf" \
              "DBus + X11 + local-bound sockets break without loopback accept."
fi

# Gate H — ICMP echo-request accept (IPv4 + IPv6) deployed.
header "Gate H — ICMP echo-request accept (IPv4 + IPv6)"
HAS_V4_PING=$(grep -cE '^[[:space:]]+(ip[[:space:]]+protocol[[:space:]]+icmp[[:space:]]+)?icmp[[:space:]]+type[[:space:]]+echo-request[[:space:]]+accept' "$NFTABLES_CONF")
HAS_V6_PING=$(grep -cE '^[[:space:]]+(ip6[[:space:]]+nexthdr[[:space:]]+ipv6-icmp[[:space:]]+)?icmpv6[[:space:]]+type[[:space:]]+echo-request[[:space:]]+accept' "$NFTABLES_CONF")
if [ "$HAS_V4_PING" -ge 1 ] && [ "$HAS_V6_PING" -ge 1 ]; then
    green "PASS — IPv4 + IPv6 echo-request accept present"
else
    violation "ICMP echo-request accept missing (v4=$HAS_V4_PING v6=$HAS_V6_PING)" \
              "D-011 verbatim: ping accept on both IPv4 + IPv6."
fi

# Gate I — PMTUd accept rules deployed (IPv4 narrowed to frag-needed
# code + IPv6 packet-too-big). The nftables canonical icmp-code name is
# "frag-needed"; the verbose form "fragmentation-needed" was rejected
# by nft at parse time and silently dropped the entire firewall.
header "Gate I — PMTUd accept (IPv4 code-narrow + IPv6 packet-too-big)"
HAS_V4_PMTUD_NARROW=$(grep -cE 'icmp[[:space:]]+type[[:space:]]+destination-unreachable[[:space:]]+icmp[[:space:]]+code[[:space:]]+frag-needed[[:space:]]+accept' "$NFTABLES_CONF")
HAS_V4_PMTUD_BROAD=$(grep -cE 'icmp[[:space:]]+type[[:space:]]+destination-unreachable[[:space:]]+accept[[:space:]]*$' "$NFTABLES_CONF")
HAS_V6_PMTUD=$(grep -cE '(ip6[[:space:]]+nexthdr[[:space:]]+ipv6-icmp[[:space:]]+)?icmpv6[[:space:]]+type[[:space:]]+packet-too-big[[:space:]]+accept' "$NFTABLES_CONF")
if [ "$HAS_V4_PMTUD_BROAD" -gt 0 ]; then
    violation "deployed /etc/nftables.conf has broad icmp destination-unreachable accept" \
              "D-011 verbatim narrows to 'code frag-needed'; broad accept admits other unauthorized codes."
elif [ "$HAS_V4_PMTUD_NARROW" -lt 1 ] || [ "$HAS_V6_PMTUD" -lt 1 ]; then
    violation "PMTUd accept missing (v4-narrow=$HAS_V4_PMTUD_NARROW v6=$HAS_V6_PMTUD)" \
              "D-011 verbatim: IPv4 destination-unreachable code frag-needed + IPv6 packet-too-big both required."
else
    green "PASS — IPv4 PMTUd code-narrowed + IPv6 packet-too-big accept present"
fi

# Gate J — IPv6 ND accept (RA + NS + NA) deployed.
header "Gate J — IPv6 ND accept (router-advert + neighbor-solicit + neighbor-advert)"
HAS_ND_RA=$(grep -cE 'nd-router-advert' "$NFTABLES_CONF")
HAS_ND_NS=$(grep -cE 'nd-neighbor-solicit' "$NFTABLES_CONF")
HAS_ND_NA=$(grep -cE 'nd-neighbor-advert' "$NFTABLES_CONF")
if [ "$HAS_ND_RA" -ge 1 ] && [ "$HAS_ND_NS" -ge 1 ] && [ "$HAS_ND_NA" -ge 1 ]; then
    green "PASS — all 3 IPv6 ND message types accept"
else
    violation "IPv6 ND accept incomplete (RA=$HAS_ND_RA NS=$HAS_ND_NS NA=$HAS_ND_NA)" \
              "D-011 verbatim: nd-router-advert + nd-neighbor-solicit + nd-neighbor-advert all required for IPv6 to function."
fi

# Gate K — ct state invalid drop deployed (defensive early-drop).
header "Gate K — ct state invalid drop"
if grep -qE '^[[:space:]]+ct[[:space:]]+state[[:space:]]+invalid[[:space:]]+drop' "$NFTABLES_CONF"; then
    green "PASS — ct state invalid drop present"
else
    violation "ct state invalid drop missing from deployed /etc/nftables.conf" \
              "Canonical defensive early-drop missing; removal would let invalid-state packets reach the kernel state machine."
fi

# Gate L — no accept rule outside the approved list, in the DEPLOYED conf.
#
# Gates F-K ask whether the accepts we need survived into the chroot. This one
# asks whether anything else got in. It matters more here than in the source
# gate: this gate exists precisely because the deployed /etc/nftables.conf can
# differ from the recipe — some other package's post_install can rewrite it —
# and an added accept is the change that most weakens the shipped posture.
header "Gate L — no accept rule outside the approved list"
UNAPPROVED=0
ACCEPTS_SEEN=0
while IFS= read -r rule; do
    case "$rule" in
        UNTERMINATED:*)
            violation "unparseable statement in deployed /etc/nftables.conf: ${rule#UNTERMINATED: }" \
                      "A statement opens a brace that never closes. The gate cannot certify a ruleset it cannot read."
            UNAPPROVED=$((UNAPPROVED + 1))
            continue
            ;;
    esac
    case "$rule" in
        *accept) ;;
        *) continue ;;
    esac
    ACCEPTS_SEEN=$((ACCEPTS_SEEN + 1))
    if ! d011_is_approved_accept "$rule"; then
        violation "unapproved accept rule in deployed /etc/nftables.conf: $rule" \
                  "Every accept in the deployed ruleset must be one of the APPROVED_ACCEPTS entries in scripts/lib/d011-accept-list.sh. A rule here that is not in the recipe means something rewrote /etc/nftables.conf during chroot assembly."
        UNAPPROVED=$((UNAPPROVED + 1))
    fi
done < <(d011_extract_rules "$NFTABLES_CONF")

ACCEPT_LINES=$(d011_count_accept_lines "$NFTABLES_CONF")
if [ "$ACCEPTS_SEEN" -eq 0 ]; then
    violation "no accept rules found in deployed /etc/nftables.conf" \
              "The deployed ruleset must carry the accepts basic networking needs; finding none means this gate did not actually read it."
elif [ "$ACCEPTS_SEEN" -ne "$ACCEPT_LINES" ]; then
    violation "the ruleset reader and the deployed /etc/nftables.conf disagree: $ACCEPTS_SEEN accept statements read, $ACCEPT_LINES accept lines in the file" \
              "A rule may be deployed that this gate never checked. Fix the deployed ruleset's structure rather than the count."
elif [ "$UNAPPROVED" -eq 0 ]; then
    green "PASS — all $ACCEPTS_SEEN deployed accept rules are on the approved list"
fi

# Summary.
header "D-011 runtime compliance summary"
if [ "$VIOLATIONS" -eq 0 ]; then
    green "ALL GATES PASS — D-011 runtime verified against $CHROOT_ROOT. Squashfs assembly may proceed."
    exit 0
else
    red "FAILED — $VIOLATIONS violation(s) found in built chroot at $CHROOT_ROOT."
    yellow "The requirement: the deployed /etc/nftables.conf runs default-deny on the input"
    yellow "and forward chains, leaves SSH (tcp/22) closed, carries the accept rules basic"
    yellow "networking needs, and carries no accept outside the APPROVED_ACCEPTS list in"
    yellow "scripts/lib/d011-accept-list.sh. The shipped ruleset it should match is"
    yellow "packages/core/intergenos-firewall-defaults/nftables.conf."
    yellow "Fix violations in the build pipeline and re-assemble the chroot."
    exit 1
fi
