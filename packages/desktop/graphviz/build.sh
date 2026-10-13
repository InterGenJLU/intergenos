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

    # The two optional components below are stated, not auto-detected. The
    # lineage built with neither present and shipped neither; the build root
    # this cycle rebuilds in carries Ghostscript (libgs) and JavaScriptCore
    # (webkitgtk), and an unstated cmake run found both and added the gs
    # loader plugin and the JavaScript binding — the configure-drift gate
    # refused the transcript for the two gained answers (measured
    # 2026-10-13). Both switches are upstream's own (CMakeLists.txt:
    # WITH_GHOSTSCRIPT, ENABLE_JAVASCRIPT); enabling either is a product
    # decision for after the tag, taken with the lineage's configuration
    # stated explicitly first.
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DWITH_GHOSTSCRIPT=OFF      \
          -DENABLE_JAVASCRIPT=OFF

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
