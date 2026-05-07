#!/bin/bash
# mpv 0.41.0 — Free media player for the command line and desktop
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..                \
          --prefix=/usr           \
          --libdir=/usr/lib       \
          --buildtype=release     \
          -Dx11=enabled           \
          -Dwayland=enabled       \
          -Ddvdnav=enabled        \
          -Dcdda=enabled
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
    # mpv is a backend for Celluloid — hide from app menu
    if [ -f "${DESTDIR}/usr/share/applications/mpv.desktop" ]; then
        echo "NoDisplay=true" >> "${DESTDIR}/usr/share/applications/mpv.desktop"
    fi
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true
}
