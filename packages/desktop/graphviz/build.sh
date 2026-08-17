#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# graphviz 14.1.2 — Graph visualization software
# BLFS 13.0

configure() {
    set -e
    # Prevent hard coding library rpath into shared libraries (BLFS)
    sed '/ORIGIN/d' -i lib/CMakeLists.txt

    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release

    # Fix gzip compression in man pages (BLFS)
    sed -i '/GZIP/s/:.*$/=/' build/CMakeCache.txt
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
