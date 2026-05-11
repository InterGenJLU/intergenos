#!/bin/bash
# pango 1.57.0 — Text layout and rendering library
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed "/docs_dir =/s@\$@ / 'pango-${PKG_VERSION}'@" -i docs/meson.build
    mkdir -p build
    cd    build

    meson setup ..                  \
          --prefix=/usr             \
          --libdir=/usr/lib         \
          --buildtype=release       \
          --wrap-mode=nofallback    \
          -Dintrospection=enabled
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    cd build
    # Three tests (test-font-data, test-font, test-layout) are known to fail per BLFS
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        ninja test
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
