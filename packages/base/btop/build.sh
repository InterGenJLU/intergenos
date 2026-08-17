#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# btop 1.4.6 — Resource monitor
# From upstream (not in BLFS)

configure() {
    set -e
    cmake -B build \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release \
          -DBUILD_TESTING=OFF
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
