#!/bin/bash
# gimp 3.0.6 — GNU Image Manipulation Program
# BLFS 13.0

configure() {
    # Apply security fixes identified upstream
    patch -Np1 -i "${IGOS_SOURCES}/gimp-3.0.6-security_fixes-1.patch"

    mkdir gimp-build
    cd    gimp-build

    meson setup ..              \
          --prefix=/usr         \
          --buildtype=release   \
          -D headless-tests=disabled
}

build() {
    cd gimp-build
    ninja
}

check() {
    cd gimp-build
    # Three tests (save-and-export, single-window-mode, ui) are known to fail
    ninja test || true
}

do_install() {
    cd gimp-build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true
}
