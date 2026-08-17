#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gexiv2 0.16.0 — GObject wrapper for Exiv2
# BLFS 13.0

configure() {
    set -e

    # Upstream 0.16 renamed its pkg-config file to gexiv2-0.16.pc but left the
    # gir's export_packages at the dead name 'gexiv2' — every consumer's
    # g-ir-scanner then probes the dead pc (surfaced by the silent-loss gate on
    # gimp's gir generation, ge9b-01 burn; authorized fix 2026-07-10).
    # Anchored fail-loud so a future version bump that moves the line surfaces
    # here instead of shipping the stale name again.
    grep -q "export_packages : 'gexiv2'," gexiv2/meson.build || {
        echo "FATAL: gexiv2 export_packages anchor not found — re-verify the gir package name against the new source"
        exit 1
    }
    sed -i "s/export_packages : 'gexiv2',/export_packages : 'gexiv2-0.16',/" gexiv2/meson.build

    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false
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
