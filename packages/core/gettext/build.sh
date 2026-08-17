#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Gettext 1.0
# LFS 13.0 Section 8.34

configure() {
    set -e
    ./configure --prefix=/usr    \
        --disable-static         \
        --docdir=/usr/share/doc/gettext-1.0
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
    chmod -v 0755 "${DESTDIR}/usr/lib/preloadable_libintl.so"
}
