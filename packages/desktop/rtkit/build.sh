#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rtkit 0.13 — RealtimeKit D-Bus service for real-time scheduling
# Required by PipeWire and GNOME Shell for real-time thread scheduling

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..                \
          --prefix=/usr           \
          --libdir=/usr/lib       \
          --buildtype=release     \
          -Dinstalled_tests=false \
          -Dlibsystemd=enabled
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
    # rtkit user/group is declared by /usr/lib/sysusers.d/rtkit.conf
    # and created by the pkm canonical sysusers hook before this
    # lifecycle hook runs.

    # Enable the service
    systemctl enable rtkit-daemon.service 2>/dev/null || true
}
