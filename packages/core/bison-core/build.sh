#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Bison 3.8.2
# LFS 13.0 Section 8.35

configure() {
    set -e
    ./configure --prefix=/usr \
        --docdir=/usr/share/doc/bison-3.8.2
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
