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

    # The five optional components below are stated, not auto-detected. The
    # lineage built with none of them present and shipped none; the build
    # root this cycle rebuilds in carries Ghostscript (libgs), JavaScriptCore
    # (webkitgtk), GDK 3, poppler-glib and librsvg, and an unstated cmake run
    # found them all: the first two surfaced as gained configure answers and
    # the other three as loader plugins (gdk, poppler, rsvg) whose shared
    # objects link libgdk-3, libgdk_pixbuf-2.0, libgio-2.0, libpoppler-glib
    # and librsvg-2 — libraries the lineage archive never needed (both
    # measured 2026-10-13, each refused by the configure-drift gate). Every
    # switch is upstream's own (CMakeLists.txt: WITH_GHOSTSCRIPT,
    # ENABLE_JAVASCRIPT, WITH_GDK, WITH_POPPLER, WITH_RSVG); enabling any of
    # them is a product decision for after the tag, taken with the lineage's
    # configuration stated explicitly first.
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DWITH_GHOSTSCRIPT=OFF      \
          -DENABLE_JAVASCRIPT=OFF     \
          -DWITH_GDK=OFF              \
          -DWITH_POPPLER=OFF          \
          -DWITH_RSVG=OFF

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
