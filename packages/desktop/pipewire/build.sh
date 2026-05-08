#!/bin/bash
# pipewire 1.6.0 — Multimedia processing framework
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    # Explicit feature flags. Build #5 audit found vulkan + the BlueZ HFP
    # (ModemManager) backend silently disabled because we relied on meson's
    # default "auto" detection. =enabled makes meson HALT if a dep is
    # missing rather than dropping the feature.
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dsession-managers=[] \
          -Dtests=disabled \
          -Dman=disabled \
          -Dvulkan=enabled \
          -Dbluez5-backend-native-mm=enabled
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
