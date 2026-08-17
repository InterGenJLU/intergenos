#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# nghttp2 1.68.1 — HTTP/2 C library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr     \
                --disable-static  \
                --enable-lib-only \
                --docdir=/usr/share/doc/nghttp2-${PKG_VERSION}
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
