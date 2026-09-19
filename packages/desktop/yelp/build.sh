#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# yelp 49.2 — GNOME help viewer (GTK 4, libadwaita, WebKit 6.0)

configure() {
    set -e
    mkdir -p build
    cd    build

    # --wrap-mode=nodownload states the policy rather than relying on it:
    # measured on the 49.2 tarball, it carries no subprojects directory and no
    # .wrap file, so nothing here would be fetched today. The flag is what
    # keeps that true if a later release gains one — a build that reaches the
    # network is a build whose inputs are not the ones that were reviewed.
    meson setup ..                \
          --prefix=/usr           \
          --libdir=/usr/lib       \
          --buildtype=release     \
          --wrap-mode=nodownload
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        ninja -C build test
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
    # A GSettings schema is not readable until the compiled cache is rebuilt,
    # and the viewer reads its own settings at startup. Every GTK application
    # in this tree rebuilds the cache here for the same reason.
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
