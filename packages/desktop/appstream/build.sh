#!/bin/bash
# appstream 1.1.2 — AppStream metadata handling library
# BLFS 13.0

configure() {
    # Tarball has ./AppStream-X.Y.Z/ prefix; strip-components=1 strips ./
    # but leaves the directory. Move contents up if needed.
    if [ -d "AppStream-${PKG_VERSION}" ]; then
        mv AppStream-${PKG_VERSION}/* .
        rmdir AppStream-${PKG_VERSION}
    fi

    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dstemming=false \
          -Dapidocs=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
