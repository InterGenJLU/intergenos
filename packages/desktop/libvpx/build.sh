#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libvpx 1.16.0 — VP8/VP9 video codec
# BLFS 13.0

configure() {
    set -e
    sed -i 's/cp -p/cp/' build/make/Makefile

    mkdir -p libvpx-build
    cd    libvpx-build

    ../configure --prefix=/usr    \
                 --enable-shared  \
                 --disable-static
}

build() {
    set -e
    cd libvpx-build
    make -j${IGOS_JOBS}
}

check() {
    set -e
    cd libvpx-build
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        env LD_LIBRARY_PATH=. make test
}

do_install() {
    set -e
    cd libvpx-build
    make DESTDIR="$DESTDIR" install
}
