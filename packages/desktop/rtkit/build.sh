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

# No post_install hook. Default enablement of every unit this package ships is
# decided in one place — intergenos-base-files'
# /usr/lib/systemd/system-preset/80-intergenos-enable.preset — and applied by the
# `systemctl preset-all` pass the image build and the installer both run. A
# `systemctl enable` here was a second voice for the same decision and the preset
# pass reverted it, so the tree stated one default and shipped another. Decided
# 2026-08-19: the preset files own this; recipes do not enable their own units.
