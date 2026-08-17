#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# seatd 0.9.3 — minimal seat management daemon + libseat.
# libseat is the session/seat-management library wlroots links for privileged
# device access. Built with the systemd-logind backend (primary on InterGenOS),
# the seatd-daemon backend, and the built-in server as a fallback path.

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..                    \
          --prefix=/usr               \
          --libdir=/usr/lib           \
          --buildtype=release         \
          -Dlibseat-logind=systemd    \
          -Dlibseat-seatd=enabled     \
          -Dlibseat-builtin=enabled   \
          -Dserver=enabled            \
          -Dexamples=disabled         \
          -Dman-pages=disabled
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
