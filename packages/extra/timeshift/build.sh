#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# timeshift 25.12.4 — system restore utility (rsync and btrfs snapshot modes).
#
# Both upstream options are enabled: -Dman=true builds the two man pages with
# help2man (Rule 15 — a tool a user cannot look up is a tool without users),
# and -Dxapp=true links the XApp library, which is what draws snapshot
# progress on the taskbar entry rather than only inside the window.
#
# The application runs as root through its polkit action, so it is built with
# the toolchain's hardening flags exactly as every other package here is; no
# recipe-local flag overrides.

configure() {
    set -e
    mkdir -p build
    cd       build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --sysconfdir=/etc     \
          --buildtype=release   \
          --wrap-mode=nodownload \
          -Dman=true            \
          -Dxapp=true
}

build() {
    set -e
    cd build
    ninja -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    set -e
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true
}
