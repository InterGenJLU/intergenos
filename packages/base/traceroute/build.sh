#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# traceroute 2.1.6 — modern Linux traceroute (TCP/UDP/ICMP methods, IPv6).
# Mirrors BLFS 13.0. Ships in place of inetutils' minimal traceroute: inetutils is
# built --disable-traceroute so it does not own /usr/bin/traceroute (collision).

configure() {
    set -e
    :  # No configure step; the package self-configures during make.
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make prefix=/usr DESTDIR="${DESTDIR}" install
    # IPv6 convenience name (BLFS): traceroute6 -> traceroute
    ln -sf traceroute "${DESTDIR}/usr/bin/traceroute6"

    # AppArmor: usr.sbin.traceroute confines
    # /usr/bin/traceroute, which this package installs. The profile
    # text is upstream's, staged by the apparmor package in
    # /usr/share/apparmor/extra-profiles, which nothing loads; this package links
    # it into /etc/apparmor.d because this package is what puts the program on
    # the machine, and the apparmor unit loads every file in /etc/apparmor.d at
    # boot. apparmor is a runtime dependency so the link cannot dangle. See
    # packages/core/apparmor/README.md, "Where a profile lives".
    install -dm755 "${DESTDIR}/etc/apparmor.d"
    for profile in usr.sbin.traceroute; do
        ln -s "../../usr/share/apparmor/extra-profiles/${profile}" \
            "${DESTDIR}/etc/apparmor.d/${profile}"
    done
}
