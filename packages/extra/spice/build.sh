#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# spice 0.16.0 — SPICE remote display server library
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# libspice-server implements the host side of the SPICE remote-display
# protocol; qemu links it to expose graphical consoles that spice-gtk /
# virt-viewer connect to. lz4 + SASL + Opus + GStreamer video encoding
# are enabled (all providers ship in-tree). smartcard is disabled: it
# requires libcacard, which the distribution does not ship and no
# consumer requests. The manual is disabled: it needs the asciidoc
# toolchain, which the distribution does not ship (library docs have no
# in-image consumer).

configure() {
    set -e
    meson setup build \
        --prefix=/usr \
        --libdir=/usr/lib \
        --buildtype=release \
        -Dgstreamer=1.0 \
        -Dlz4=true \
        -Dsasl=true \
        -Dopus=enabled \
        -Dsmartcard=disabled \
        -Dmanual=false
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install
}
