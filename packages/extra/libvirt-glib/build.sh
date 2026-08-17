#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libvirt-glib 5.0.0 — GLib/GObject bindings for libvirt
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# GObject-mapped libvirt API. virt-manager consumes it through the
# introspection typelibs (load-bearing); vala and docs are disabled
# (neither toolchain is in the distribution).

configure() {
    set -e
    meson setup build \
        --prefix=/usr \
        --libdir=/usr/lib \
        --buildtype=release \
        -Dintrospection=enabled \
        -Dvapi=disabled \
        -Ddocs=disabled
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install
}
