#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# usbutils 019 — the tools that answer "what is plugged into this machine?"
# (lsusb, usb-devices, usbhid-dump).
#
# Build system verified against the pinned tarball:
#   - meson (meson.build at the top level; autogen.sh is for git checkouts).
#   - Exactly two dependencies: libusb-1.0 >= 1.0.22 and libudev >= 196
#     (meson.build:108-109). libudev comes from systemd in this tree.
#   - Installed executables: lsusb and usbhid-dump (meson.build:111,129) plus
#     the usb-devices and lsusb.py scripts installed into bindir
#     (meson.build:150,158). usbreset is deliberately NOT installed upstream
#     (`install: false`, meson.build:140), so it is absent here too rather than
#     being force-installed.
#
# Device NAMES come from the udev hardware database, not from a bundled
# usb.ids file: names.c calls udev_hwdb_new() and reads ID_VENDOR_FROM_DATABASE
# / ID_MODEL_FROM_DATABASE (names.c:31,84-132,245). That is why systemd is a
# runtime dependency and hwdata is not — this package needs the hwdb systemd
# builds, and adding a desktop-tier data package as a base-tier runtime
# dependency would be both unnecessary and a backward reach.

configure() {
    set -e
    meson setup build       \
          --prefix=/usr     \
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
