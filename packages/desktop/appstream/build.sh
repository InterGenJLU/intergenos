#!/bin/bash
# appstream 1.1.2 — AppStream metadata handling library
# BLFS 13.0

configure() {
    set -e
    # Tarball has ./AppStream-X.Y.Z/ prefix; strip-components=1 strips ./
    # but leaves the directory. Flatten contents up if needed.
    # Use 'cp -a SRC/.' idiom (handles dotfiles like .editorconfig that
    # bare 'mv SRC/*' would skip — caused rmdir failure 2026-05-07).
    if [ -d "AppStream-${PKG_VERSION}" ]; then
        cp -a "AppStream-${PKG_VERSION}"/. .
        rm -rf "AppStream-${PKG_VERSION}"
    fi

    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dstemming=false \
          -Dapidocs=false
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

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
