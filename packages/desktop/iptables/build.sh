#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# iptables 1.8.12 — Linux kernel packet filtering framework
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --enable-nftables \
                --enable-libipq
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Point the plain-named frontends at the nftables backend.
    #
    # WHY THIS IS NEEDED. This distribution is nftables-only: the kernel
    # fragment turns the legacy xtables interface off and the shipped kernel
    # carries no ip_tables modules. Upstream's `make install` nevertheless
    # links the UNSUFFIXED names — iptables, iptables-restore, iptables-save
    # and their IPv6 twins — to the LEGACY multi-call binary, and nothing here
    # repointed them. Every program that shells out to `iptables` therefore
    # failed at its first call with "modprobe: FATAL: Module ip_tables not
    # found": measured on four installed machines, where the mesh networking
    # daemon's own health surface named the failing command and none of the
    # packet-filter chains it needs existed. Container and virtual-machine
    # tooling call the same binary. arptables and ebtables, installed by this
    # same step, already pointed at the nftables backend — which is what made
    # the other six a symlink nobody updated rather than a decision.
    #
    # WHAT IS DELIBERATELY LEFT ALONE. The frontends whose names SAY legacy
    # (iptables-legacy and friends) keep pointing at the legacy binary, and the
    # legacy binary itself is still installed. Fixing a default is not the same
    # decision as removing a capability a user asked for by name.
    local sbin="${DESTDIR}/usr/sbin"
    local frontends="iptables iptables-restore iptables-save"
    frontends="${frontends} ip6tables ip6tables-restore ip6tables-save"
    local f
    for f in ${frontends}; do
        if [ ! -e "${sbin}/${f}" ]; then
            echo "iptables: ${sbin}/${f} was not installed; upstream's layout" \
                 "changed and this relink no longer describes it" >&2
            exit 1
        fi
        ln -sfn xtables-nft-multi "${sbin}/${f}"
    done

    # Read the result back. A relink that silently did not happen would ship the
    # same defect under a recipe that claims to have fixed it.
    for f in ${frontends}; do
        if [ "$(readlink "${sbin}/${f}")" != "xtables-nft-multi" ]; then
            echo "iptables: ${sbin}/${f} still resolves to" \
                 "$(readlink "${sbin}/${f}")" >&2
            exit 1
        fi
    done
}
