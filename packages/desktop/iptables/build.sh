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
}
