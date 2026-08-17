#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# flac 1.5.0 — Free Lossless Audio Codec
# BLFS 13.0 (uses autotools, not cmake)

configure() {
    set -e
    ./configure --prefix=/usr            \
                --disable-thorough-tests \
                --docdir=/usr/share/doc/flac-${version}
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
