#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# scripts/check-d011-compliance.sh — D-011 compliance gate.
#
# THE REQUIREMENT (D-011, decided 2026-05-19). The shipped firewall runs
# default-deny: the input and forward chains both have policy drop, SSH
# (tcp/22) is closed, the ruleset carries the accept rules basic networking
# needs and no others, and exactly one package owns /etc/nftables.conf.
# This is a Class A gate — it blocks ISO/qcow2 creation until that posture
# holds.
#
# WHERE THE REQUIREMENT IS RECORDED IN THIS REPOSITORY:
#   packages/core/intergenos-firewall-defaults/nftables.conf
#       the shipped ruleset itself, with the reasoning for each rule in its
#       own comments;
#   packages/core/intergenos-firewall-defaults/build.sh
#       the package's own build-time asserts on the policy lines;
#   scripts/lib/d011-accept-list.sh
#       APPROVED_ACCEPTS — the authorized accept rules, one line each, with
#       what authorizes it, shared with the runtime gate;
#   tests/preflight/test_firewall_libvirt_bridge_accept.py
#   tests/preflight/test_d011_accept_whitelist.py
#       the pytest half, which reads APPROVED_ACCEPTS out of that library
#       rather than restating it, so the enforcement layers cannot drift.
#
# Run before `phase_image` (and any ISO-assembly path) in
# scripts/build-intergenos.sh. May also be invoked standalone:
#   scripts/check-d011-compliance.sh
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

DEFAULTS_CONF="packages/core/intergenos-firewall-defaults/nftables.conf"

# The approved accept list, the ruleset reader and the membership predicate
# live in scripts/lib/d011-accept-list.sh — one record, shared with the
# runtime gate and read directly by the pytest half, so the layers cannot
# drift apart. Sourcing is FAIL-CLOSED: a gate that cannot load its own
# approved list certifies nothing.
D011_LIB="$REPO_ROOT/scripts/lib/d011-accept-list.sh"
if [ ! -f "$D011_LIB" ]; then
    red "FATAL: $D011_LIB missing — cannot evaluate the approved accept list."
    exit 2
fi
# shellcheck source=lib/d011-accept-list.sh
source "$D011_LIB"

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

# Gate D was "install-theming.sh contains no firewall write block" — retired
# 2026-05-22 evening CST along with install-theming.sh itself (divestment
# trajectory complete). The script no longer exists, so the regression class
# this gate guarded against is mechanically impossible. intergenos-firewall-
# defaults remains the sole writer of /etc/nftables.conf.

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

# ----- Positive-rule presence gates (the approved accept list) -----
#
# Gates A-F cover policy posture + retired-block absence + service enablement.
# Gates G-L verify each accept rule basic networking needs is present. Drift
# on any one breaks that networking (e.g., losing IPv6 ND breaks IPv6
# entirely; losing ct established,related makes every outbound connection
# one-way at the kernel level). The authorized set is APPROVED_ACCEPTS in
# scripts/lib/d011-accept-list.sh; Gate M enforces the other direction, that
# nothing outside it was added.

# Gate G: ct state established,related accept (without this, every outbound
# connection one-ways at SYN-ACK)
header "Gate G — ct state established,related accept present"
if [ -f "$DEFAULTS_CONF" ] && grep -qE '^[[:space:]]+ct[[:space:]]+state[[:space:]]+established,related[[:space:]]+accept' "$DEFAULTS_CONF"; then
    green "PASS — ct state established,related accept present"
else
    violation "ct state established,related accept missing from $DEFAULTS_CONF" \
              "D-011 verbatim: outbound responses inbound require this state-based accept."
fi

# Gate H: loopback (iif "lo") accept (without this, DBus + X11 + local-bound
# sockets break)
header "Gate H — loopback (iif lo) accept present"
if [ -f "$DEFAULTS_CONF" ] && grep -qE '^[[:space:]]+iif[[:space:]]+"?lo"?[[:space:]]+accept' "$DEFAULTS_CONF"; then
    green "PASS — iif lo accept present"
else
    violation "iif lo accept missing from $DEFAULTS_CONF" \
              "D-011 verbatim: loopback accept is mandatory; intra-host services break without it."
fi

# Gate I: ICMP echo-request accept (IPv4 + IPv6 both — without these, ping
# fails to the host from peers on the LAN)
header "Gate I — ICMP echo-request accept present (IPv4 + IPv6)"
if [ ! -f "$DEFAULTS_CONF" ]; then
    violation "$DEFAULTS_CONF missing"
else
    HAS_V4_PING=$(grep -cE '^[[:space:]]+(ip[[:space:]]+protocol[[:space:]]+icmp[[:space:]]+)?icmp[[:space:]]+type[[:space:]]+echo-request[[:space:]]+accept' "$DEFAULTS_CONF")
    HAS_V6_PING=$(grep -cE '^[[:space:]]+(ip6[[:space:]]+nexthdr[[:space:]]+ipv6-icmp[[:space:]]+)?icmpv6[[:space:]]+type[[:space:]]+echo-request[[:space:]]+accept' "$DEFAULTS_CONF")
    if [ "$HAS_V4_PING" -ge 1 ] && [ "$HAS_V6_PING" -ge 1 ]; then
        green "PASS — IPv4 echo-request + IPv6 echo-request both accept"
    else
        violation "ICMP echo-request accept missing from $DEFAULTS_CONF (v4=$HAS_V4_PING v6=$HAS_V6_PING)" \
                  "D-011 verbatim: ping accept on both IPv4 + IPv6."
    fi
fi

# Gate J: PMTUd accept rules (IPv4 destination-unreachable narrowed to code
# frag-needed + IPv6 packet-too-big). The IPv4 code-narrow is D-011
# verbatim line 488 — wider destination-unreachable would admit other codes
# (host-unreachable, port-unreachable, protocol-unreachable etc.) not
# authorized by the directive.
header "Gate J — PMTUd accept rules present (IPv4 code-narrow + IPv6 packet-too-big)"
if [ ! -f "$DEFAULTS_CONF" ]; then
    violation "$DEFAULTS_CONF missing"
else
    HAS_V4_PMTUD_NARROW=$(grep -cE 'icmp[[:space:]]+type[[:space:]]+destination-unreachable[[:space:]]+icmp[[:space:]]+code[[:space:]]+frag-needed[[:space:]]+accept' "$DEFAULTS_CONF")
    HAS_V4_PMTUD_BROAD=$(grep -cE 'icmp[[:space:]]+type[[:space:]]+destination-unreachable[[:space:]]+accept[[:space:]]*$' "$DEFAULTS_CONF")
    HAS_V6_PMTUD=$(grep -cE '(ip6[[:space:]]+nexthdr[[:space:]]+ipv6-icmp[[:space:]]+)?icmpv6[[:space:]]+type[[:space:]]+packet-too-big[[:space:]]+accept' "$DEFAULTS_CONF")
    if [ "$HAS_V4_PMTUD_BROAD" -gt 0 ]; then
        violation "$DEFAULTS_CONF has broad icmp destination-unreachable accept" \
                  "D-011 verbatim narrows to 'code frag-needed'; broad accept admits other unauthorized codes."
    elif [ "$HAS_V4_PMTUD_NARROW" -lt 1 ] || [ "$HAS_V6_PMTUD" -lt 1 ]; then
        violation "PMTUd accept missing from $DEFAULTS_CONF (v4-narrow=$HAS_V4_PMTUD_NARROW v6=$HAS_V6_PMTUD)" \
                  "D-011 verbatim: IPv4 destination-unreachable code frag-needed + IPv6 packet-too-big both required."
    else
        green "PASS — IPv4 PMTUd code-narrowed + IPv6 packet-too-big accept present"
    fi
fi

# Gate K: IPv6 Neighbor Discovery accept (nd-router-advert + nd-neighbor-
# solicit + nd-neighbor-advert) — without these, IPv6 doesn't function
header "Gate K — IPv6 ND accept present (router-advert + neighbor-solicit + neighbor-advert)"
if [ ! -f "$DEFAULTS_CONF" ]; then
    violation "$DEFAULTS_CONF missing"
else
    HAS_ND_RA=$(grep -cE 'nd-router-advert' "$DEFAULTS_CONF")
    HAS_ND_NS=$(grep -cE 'nd-neighbor-solicit' "$DEFAULTS_CONF")
    HAS_ND_NA=$(grep -cE 'nd-neighbor-advert' "$DEFAULTS_CONF")
    if [ "$HAS_ND_RA" -ge 1 ] && [ "$HAS_ND_NS" -ge 1 ] && [ "$HAS_ND_NA" -ge 1 ]; then
        green "PASS — all 3 IPv6 ND message types accept"
    else
        violation "IPv6 ND accept incomplete in $DEFAULTS_CONF (RA=$HAS_ND_RA NS=$HAS_ND_NS NA=$HAS_ND_NA)" \
                  "D-011 verbatim: nd-router-advert + nd-neighbor-solicit + nd-neighbor-advert all required for IPv6 to function."
    fi
fi

# Gate L: ct state invalid drop (defensive early-drop; canonical
# intergenos-firewall-defaults includes this for state-machine validation)
header "Gate L — ct state invalid drop present (defensive early-drop)"
if [ -f "$DEFAULTS_CONF" ] && grep -qE '^[[:space:]]+ct[[:space:]]+state[[:space:]]+invalid[[:space:]]+drop' "$DEFAULTS_CONF"; then
    green "PASS — ct state invalid drop present"
else
    violation "ct state invalid drop missing from $DEFAULTS_CONF" \
              "Canonical intergenos-firewall-defaults includes this defensive early-drop; removal would let invalid-state packets reach the kernel state machine."
fi

# Gate M: no accept rule outside the approved list.
#
# The gates above ask whether the rules we need are present. This one asks
# whether anything else got in — an added port, a rule that lost its
# interface scope, an accept appended to the forward chain. Any accept
# statement in the shipped ruleset that is not character-for-character one of
# APPROVED_ACCEPTS fails the build.
header "Gate M — no accept rule outside the approved list"
if [ ! -f "$DEFAULTS_CONF" ]; then
    violation "$DEFAULTS_CONF missing"
else
    UNAPPROVED=0
    ACCEPTS_SEEN=0
    while IFS= read -r rule; do
        case "$rule" in
            UNTERMINATED:*)
                violation "unparseable statement in $DEFAULTS_CONF: ${rule#UNTERMINATED: }" \
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
            violation "unapproved accept rule in $DEFAULTS_CONF: $rule" \
                      "Every accept in the shipped default-deny ruleset must be one of the APPROVED_ACCEPTS entries in scripts/lib/d011-accept-list.sh. If this rule was decided on, record it there in the same change; if it was not, it does not belong in the shipped ruleset."
            UNAPPROVED=$((UNAPPROVED + 1))
        fi
    done < <(d011_extract_rules "$DEFAULTS_CONF")

    ACCEPT_LINES=$(d011_count_accept_lines "$DEFAULTS_CONF")
    if [ "$ACCEPTS_SEEN" -eq 0 ]; then
        # A ruleset with no accepts at all means the reader read nothing
        # useful — a zero result the gate must not certify as clean.
        violation "no accept rules found in $DEFAULTS_CONF" \
                  "The ruleset must carry the accepts basic networking needs; finding none means this gate did not actually read the ruleset."
    elif [ "$ACCEPTS_SEEN" -ne "$ACCEPT_LINES" ]; then
        violation "the ruleset reader and $DEFAULTS_CONF disagree: $ACCEPTS_SEEN accept statements read, $ACCEPT_LINES accept lines in the file" \
                  "A rule may be present that this gate never checked. Fix the ruleset's structure rather than the count."
    elif [ "$UNAPPROVED" -eq 0 ]; then
        green "PASS — all $ACCEPTS_SEEN accept rules are on the approved list"
    fi
fi

# Summary
header "D-011 compliance summary"
if [ "$VIOLATIONS" -eq 0 ]; then
    green "ALL GATES PASS — D-011 compliance verified. Build may proceed."
    exit 0
else
    red "FAILED — $VIOLATIONS violation(s) found."
    yellow "The requirement: the shipped firewall runs default-deny on the input and"
    yellow "forward chains, leaves SSH (tcp/22) closed, carries the accept rules basic"
    yellow "networking needs, and carries no accept outside the APPROVED_ACCEPTS list in"
    yellow "scripts/lib/d011-accept-list.sh."
    yellow "The shipped ruleset is packages/core/intergenos-firewall-defaults/nftables.conf;"
    yellow "each rule's reasoning is in that file's own comments."
    yellow "Fix violations and re-run before ISO/qcow2 assembly may proceed."
    exit 1
fi
