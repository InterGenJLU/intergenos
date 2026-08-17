#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Xz Utils 5.8.2
# LFS 13.0 Section 8.8

configure() {
    set -e
    ./configure --prefix=/usr    \
        --disable-static         \
        --docdir=/usr/share/doc/xz-5.8.2
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
