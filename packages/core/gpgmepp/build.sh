#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gpgmepp 2.0.0 — C++ wrapper for GPGME
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    cmake -D CMAKE_INSTALL_PREFIX=/usr ..
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" make install
}
