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
}
