#!/bin/bash
# hicolor-icon-theme 0.18 — Default fallback icon theme
# BLFS 13.0

configure() {
    mkdir build
    cd    build
    meson setup .. --prefix=/usr --libdir=/usr/lib
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
    gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
}
