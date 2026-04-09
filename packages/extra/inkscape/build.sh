#!/bin/bash
# inkscape 1.4.3 — Vector graphics editor with SVG support
# BLFS 13.0
#
# Note: The tarball extracts to inkscape-1.4.3_2025-12-25_0d15f75042/

configure() {
    # Fix build failures with poppler-26.02.0
    patch -Np1 -i "${IGOS_SOURCES}/inkscape-1.4.3-poppler_26.02-1.patch"

    # Fix build failures with poppler-26.01.0
    sed -i 's/gfree/g_free/' src/extension/internal/pdfinput/pdf-input.cpp

    sed -e '/Stream.h/a#include <poppler/goo/gmem.h>' \
        -e 's/reset/rewind/'                          \
        -i src/extension/internal/pdfinput/svg-builder.cpp

    mkdir build
    cd    build

    cmake -D CMAKE_INSTALL_PREFIX=/usr  \
          -D CMAKE_BUILD_TYPE=Release   \
          -W no-dev                     \
          ..
}

build() {
    cd build
    make -j${IGOS_JOBS}
}

do_install() {
    cd build
    make DESTDIR="$DESTDIR" install
}

post_install() {
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true
}
