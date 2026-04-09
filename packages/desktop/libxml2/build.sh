#!/bin/bash
# libxml2 2.15.1 — XML parsing library
# BLFS 13.0 (meson build with Python bindings)

configure() {
    # BLFS: remove unnecessary git call
    sed -i "/'git'/,+3d" meson.build

    # Remove hard doxygen dependency in doc/meson.build.
    # The doc subdir is entered for Python bindings even with -Ddocs=disabled,
    # and doxygen isn't built yet (circular: doxygen depends on libxml2).
    # Replace the entire doxygen block with a stub so meson doesn't error.
    sed -i "s/^doxygen = find_program.*$/doxygen = disabler()/" doc/meson.build

    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib   \
          -Dhistory=enabled     \
          -Dicu=enabled         \
          -Dpython=enabled      \
          -Ddocs=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
