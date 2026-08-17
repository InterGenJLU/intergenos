#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gnome-session 49.2 — GNOME session manager
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Ddocbook=false \
          -Dman=false
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

    # #5: scrub dangling gnome-software handlers from gnome-mimeapps.list.
    # Upstream points flatpak/snap/fwupd/appstream MIME types + schemes at
    # gnome-software-local-file-*.desktop / org.gnome.Software.desktop, but
    # InterGenOS does NOT ship gnome-software (pkm is our package manager).
    # Leaving these dangling makes "Open With" offer / try to launch a
    # nonexistent gnome-software ("Failed to execute child process
    # gnome-software", seen on the development machine install). Remove the dead lines so the
    # affected types fall back cleanly to "no handler".
    local mimeapps="${DESTDIR}/usr/share/applications/gnome-mimeapps.list"
    if [ -f "$mimeapps" ]; then
        sed -i '/gnome-software-local-file-/d; /=org\.gnome\.Software\.desktop/d' "$mimeapps"
    fi
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
    update-desktop-database /usr/share/applications 2>/dev/null || true
}
