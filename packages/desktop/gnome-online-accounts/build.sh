#!/bin/bash
# gnome-online-accounts 3.56.4 — GNOME online accounts service
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    # kerberos=true: enterprise auth in cloud-account flows (Exchange,
    # Office 365, etc.). mitkrb is tier:core (reclassified 2026-05-10);
    # already declared as build dep.
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Ddocumentation=false \
          -Dman=false \
          -Dkerberos=true
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
