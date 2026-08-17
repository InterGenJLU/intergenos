# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
# d011-accept-list.sh — the approved accept list for the shipped default-deny
# firewall, plus the ruleset reader the D-011 gates check it with.
#
# Single source of truth, sourced by:
#   - scripts/check-d011-compliance.sh  (Gate M, against the recipe in the tree)
#   - scripts/check-d011-runtime.sh     (Gate L, against the built chroot)
#   - tests/preflight/test_d011_accept_whitelist.py reads APPROVED_ACCEPTS out
#     of THIS file rather than restating it, so the pytest half and the build
#     gates cannot drift apart.
#
# This file is meant to be SOURCED — it defines a list and two functions and
# runs nothing.
#
# WHY THE LIST EXISTS. The D-011 gates ask whether the accept rules basic
# networking needs are present. That direction alone cannot see an ADDITION:
# before this list, adding a rule that opened a new port to the shipped
# ruleset — or widening one of the existing rules until it lost its interface
# scope — produced byte-identical gate output, so the gate that blocks image
# creation could not fail on the change most likely to weaken the posture.
#
# Matching is EXACT. A rule that is reworded, rescoped or widened does not
# match its entry and fails the gate, which is the point: what the shipped
# default-deny ruleset accepts is a decision, and this list is where that
# decision is recorded. Adding an entry is therefore the deliberate act — do
# not add one to make a build green, add one because the accept it names was
# decided on, in the same change that adds the rule.

# Every accept statement the shipped ruleset is authorized to carry, written
# as the extractor below emits it: one complete nftables statement, single
# spaces, set members joined onto one line.
APPROVED_ACCEPTS=(
    # Loopback. Intra-host services (DBus, X11, sockets bound to 127.0.0.1)
    # break without it.
    'iif "lo" accept'
    # Responses to outbound connections. Without it every outbound connection
    # is one-way and TCP hangs at SYN-ACK.
    'ct state established,related accept'
    # Ping, both families.
    'ip protocol icmp icmp type echo-request accept'
    'ip6 nexthdr ipv6-icmp icmpv6 type echo-request accept'
    # Path-MTU discovery. The IPv4 rule is narrowed to the frag-needed code; a
    # broad destination-unreachable accept would also admit host-unreachable,
    # port-unreachable and protocol-unreachable, and is refused separately.
    'ip protocol icmp icmp type destination-unreachable icmp code frag-needed accept'
    'ip6 nexthdr ipv6-icmp icmpv6 type packet-too-big accept'
    # IPv6 Neighbor Discovery. Without all three types IPv6 does not function.
    'ip6 nexthdr ipv6-icmp icmpv6 type { nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert } accept'
    # DHCP and DNS for libvirt NAT guests, scoped to the virtual-bridge
    # interface class and to nothing else (decided 2026-08-04). These ports
    # stay closed on every real interface, and no interface carries this name
    # unless libvirt is installed with a network started.
    'iifname "virbr*" udp dport 67 accept'
    'iifname "virbr*" udp dport 53 accept'
    'iifname "virbr*" tcp dport 53 accept'
)

# d011_extract_rules <nftables.conf> — print every rule statement in the file,
# one per line, whitespace-normalized, comments stripped, multi-line set
# statements joined onto a single line.
#
# Table and chain headers, `type filter hook ... policy ...;` policy lines,
# `flush ruleset` and `include` are structural rather than rules and are not
# printed. A statement that opens a brace (an anonymous set such as
# `icmpv6 type { ... } accept`) accumulates until that brace closes, so a set
# spread over several lines is compared as the one statement it is.
#
# A statement whose brace never closes is printed as `UNTERMINATED: <text>`
# rather than dropped: a ruleset this reader cannot parse is one the gate must
# refuse, never one it silently certifies.
d011_extract_rules() {
    awk '
        function emit(s) {
            gsub(/[ \t]+/, " ", s)
            gsub(/\{ +/, "{ ", s)
            gsub(/ +\}/, " }", s)
            sub(/^ +/, "", s); sub(/ +$/, "", s)
            if (s != "") print s
        }
        {
            line = $0
            sub(/#.*/, "", line)
            gsub(/^[ \t]+|[ \t]+$/, "", line)
            if (line == "") next
            if (depth == 0) {
                if (line ~ /^table[ \t]/ || line ~ /^chain[ \t]/) next
                if (line == "}" || line == "{") next
                if (line ~ /^type[ \t]+filter[ \t]+hook[ \t]/) next
                if (line ~ /^flush[ \t]/ || line ~ /^include[ \t]/) next
                stmt = line
            } else {
                stmt = stmt " " line
            }
            depth += gsub(/\{/, "{", line) - gsub(/\}/, "}", line)
            if (depth <= 0) { depth = 0; emit(stmt); stmt = "" }
        }
        END { if (stmt != "") print "UNTERMINATED: " stmt }
    ' "$1"
}

# d011_count_accept_lines <nftables.conf> — how many non-comment lines END in
# the accept verdict.
#
# This is a deliberately dumb second instrument, independent of the reader
# above. Every accept statement ends on exactly one line, whether or not it
# spans several, so this count must equal the number of accept statements
# d011_extract_rules emits. If the two disagree, the reader's brace tracking
# has been thrown off — a malformed set can swallow a chain's closing brace and
# shift everything after it — and a rule could be sitting in the ruleset that
# the reader never presented for checking. The gates treat a disagreement as a
# failure rather than trusting the smarter of the two.
d011_count_accept_lines() {
    sed 's/#.*//' "$1" \
        | grep -cE '(^|[[:space:]])accept[[:space:]]*$'
}

# d011_is_approved_accept <statement> — rc 0 when the statement is exactly one
# of the approved accepts.
d011_is_approved_accept() {
    local candidate="$1" approved
    for approved in "${APPROVED_ACCEPTS[@]}"; do
        [ "$candidate" = "$approved" ] && return 0
    done
    return 1
}
