#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gnome-terminal 3.58.1 — GNOME terminal emulator
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -i -r 's:"(/system):"/org/gnome\1:g' src/external.gschema.xml

    # InterGenOS patches — applied from packages/desktop/gnome-terminal/patches/.
    # The build environment sets IGOS_PACKAGE_DIR to the package recipe
    # directory; fall back to the canonical workspace path if unset (some
    # surgical-rebuild invocations don't propagate it).
    local patches_dir="${IGOS_PACKAGE_DIR:-/mnt/intergenos/packages/desktop/gnome-terminal}/patches"
    if [ -d "$patches_dir" ]; then
        for p in "$patches_dir"/*.patch; do
            [ -f "$p" ] || continue
            echo "Applying patch: $(basename "$p")"
            patch -p1 -i "$p"
        done
    fi

    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release 
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
