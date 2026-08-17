#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# spice-protocol 0.14.5 — SPICE protocol headers
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# Headers-only package defining the SPICE remote-display wire protocol.
# Build-time dependency of spice (server), spice-gtk (client), and qemu.
# Installs headers under /usr/include/spice-1 and an arch-independent
# pkg-config file under /usr/share/pkgconfig.

configure() {
    set -e
    meson setup build \
        --prefix=/usr \
        --buildtype=release
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install
}
