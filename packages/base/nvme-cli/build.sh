#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# nvme-cli 2.16 — management and inspection of NVMe solid-state storage.
#
# The library half (libnvme) is already in the tree as a core package; this is
# the command-line consumer of it.
#
# Build system verified against the pinned tarball:
#   - meson. The binary installs into sbindir (meson.build:318-325), which the
#     configuration below sets to /usr/sbin, so the tool lands at /usr/sbin/nvme.
#   - Required dependencies are libnvme >= 1.16 and libnvme-mi, both provided by
#     the core libnvme package; json-c is a `feature` defaulting to auto and is
#     requested explicitly here so the JSON output mode is present by decision
#     rather than by whatever happens to be installed in the chroot.
#   - Man pages are pre-built in Documentation/ and installed by -Ddocs=man.
#     -Ddocs-build=false keeps the build from regenerating them, which would
#     pull in asciidoc and xmlto as build dependencies for no gain: upstream's
#     shipped pages are the same content.
#   - The device-driving test suite is left off (-Dnvme-tests=false); see the
#     reason recorded in package.yml.

configure() {
    set -e
    meson setup build         \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --sbindir=/usr/sbin \
          --buildtype=release \
          -Ddocs=man          \
          -Ddocs-build=false  \
          -Djson-c=enabled    \
          -Dnvme-tests=false
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install
}
