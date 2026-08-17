#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# wireplumber 0.5.13 — PipeWire session manager
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release       \
          --wrap-mode=nofallback    \
          -Dsystem-lua=true ..
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
    # The PulseAudio-conflict file removals + client.conf autospawn edit moved
    # to the pulseaudio recipe's staging (hook-contract wave: no cross-package
    # mutation from hooks).
    systemctl enable --global pipewire.socket 2>/dev/null || true
    systemctl enable --global pipewire-pulse.socket 2>/dev/null || true
    systemctl enable --global wireplumber 2>/dev/null || true
}
