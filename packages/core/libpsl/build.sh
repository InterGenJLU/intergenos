#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libpsl 0.21.5 — Public Suffix List library
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup --prefix=/usr     \
        --libdir=/usr/lib         \
        --buildtype=release ..
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    cd build
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        ninja test
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
