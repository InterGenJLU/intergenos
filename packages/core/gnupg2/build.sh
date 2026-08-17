#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gnupg2 2.5.17 — GNU Privacy Guard
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --localstatedir=/var \
                --sysconfdir=/etc
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
