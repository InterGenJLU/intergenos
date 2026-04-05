#!/bin/bash
# pango 1.57.0 — Text layout and rendering library
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed "/docs_dir =/s@\$@ / 'pango-${PKG_VERSION}'@" -i docs/meson.build
    mkdir build
    cd    build

    meson setup ..                  \
          --prefix=/usr             \
          --libdir=/usr/lib         \
          --buildtype=release       \
          --wrap-mode=nofallback    \
          -Dintrospection=enabled
}

build() {
    cd build
    ninja
}

check() {
    cd build
    # Three tests (test-font-data, test-font, test-layout) are known to fail per BLFS
    ninja test || true
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
