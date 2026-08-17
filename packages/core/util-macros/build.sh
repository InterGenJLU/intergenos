#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# util-macros 1.20.2 — Xorg autotools macros
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --localstatedir=/var
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
