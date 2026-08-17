#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# fuse3 3.18.1 — Filesystem in Userspace
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -i '/^udev/,$ s/^/#/' util/meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dexamples=false \
          -Duseroot=false
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

    # Set setuid bit — fusermount3 needs setuid for non-root FUSE mounts.
    # Must be set here because tar-based deployment strips setuid bits.
    chmod 4755 "${DESTDIR}/usr/bin/fusermount3"
}
