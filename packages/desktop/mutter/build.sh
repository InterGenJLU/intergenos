#!/bin/bash
# mutter 49.4 — GNOME window manager and Wayland compositor
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed "/tests_c_args =/s/\$/ + ['-U', 'G_DISABLE_ASSERT']/" -i src/tests/meson.build
    sed "/c_args:/a '-U', 'G_DISABLE_ASSERT'," -i src/tests/cogl/unit/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=disabled \
          -Ddocs=false \
          -Dprofiler=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
