#!/bin/bash
# intergenos-firewall-defaults 1.0.0 — InterGenOS default-deny firewall policy
# https://github.com/InterGenJLU/intergenos
#
# Per D-011 (2026-05-19 owner directive), this package is THE source
# of the system-wide firewall policy on InterGenOS. It ships:
#
#   /etc/nftables.conf — default-deny INPUT/FORWARD ruleset
#
# The upstream packages/core/nftables/ package stays policy-neutral
# (it ships the nftables tool + the systemd unit only; it does NOT
# ship /etc/nftables.conf since D-011 ratification).
#
# Users who want to take their firewall in hand:
#   pkm remove intergenos-firewall-defaults
# The nftables tool itself stays installed; the user writes
# /etc/nftables.conf themselves (or masks nftables.service).
#
# Composes with packages/core/nftables/ (the tool + service) per
# the same separation pattern Fedora uses for firewalld vs upstream
# iptables/nftables, Ubuntu uses for ufw vs upstream, and openSUSE
# uses for SuSEfirewall2 / firewalld vs upstream.
#
# Audit rows closed:
#   - G-005 (HG): nftables policy=accept on every chain — fixed via
#     this package shipping policy=drop.
#   - J-021: install-theming.sh wrote a conflicting /etc/nftables.conf
#     in addition to the upstream's. This commit retires the install-
#     theming.sh firewall write block; intergenos-firewall-defaults
#     becomes the sole writer.
#
# tier=core: default firewall posture is core system policy on
# InterGenOS, not optional.

build() {
    set -e
    # No build step — config file shipped verbatim from the package
    # source directory.
    return 0
}

do_install() {
    set -e
    local pkg_dir
    pkg_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

    # Ship /etc/nftables.conf. This file is the sole writer per D-011;
    # the upstream nftables package no longer ships /etc/nftables.conf
    # since the D-011 ratification commit.
    install -Dm644 "$pkg_dir/nftables.conf" "${DESTDIR}/etc/nftables.conf"

    # Defensive assert: confirm the ruleset stages with policy drop on
    # input + forward chains, and confirms port 22 is NOT in the
    # accept rules. D-011 Gate (a) + (b) at compliance-check time
    # mirror these checks against the assembled rootfs; this in-build
    # assert catches drift at the package-author layer.
    local installed_conf="${DESTDIR}/etc/nftables.conf"
    if ! grep -qE '^[[:space:]]+chain input \{[[:space:]]*$' "$installed_conf"; then
        echo "FATAL: input chain definition missing from /etc/nftables.conf" >&2
        exit 1
    fi
    if ! grep -qE '^[[:space:]]+type filter hook input priority filter; policy drop;' "$installed_conf"; then
        echo "FATAL: input chain policy is not 'drop' per D-011" >&2
        echo "Source path: $pkg_dir/nftables.conf" >&2
        exit 1
    fi
    if ! grep -qE '^[[:space:]]+type filter hook forward priority filter; policy drop;' "$installed_conf"; then
        echo "FATAL: forward chain policy is not 'drop' per D-011" >&2
        exit 1
    fi
    if grep -qE 'tcp\s+dport\s+22\s+.*accept' "$installed_conf"; then
        echo "FATAL: tcp/22 (SSH) is allowed by default in /etc/nftables.conf" >&2
        echo "D-011 mandates SSH closed-by-default. Remove the accept rule." >&2
        exit 1
    fi
}
