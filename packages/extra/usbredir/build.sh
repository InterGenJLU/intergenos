#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# usbredir 0.15.0 — USB network redirection protocol libraries and tools
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# Protocol parser + host libraries for redirecting USB devices over the
# network, plus the usbredirect tool. Consumed by qemu and spice-gtk for
# USB passthrough into VMs. Meson build; libusb + glib2.

configure() {
    set -e
    meson setup build \
        --prefix=/usr \
        --libdir=/usr/lib \
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
