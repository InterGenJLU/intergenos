#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libcap-ng 0.9.3 — POSIX capabilities library and utilities
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# Library + utilities (pscap, filecap, netcap, captest) for working with
# POSIX capabilities. Consumed by qemu, libvirt, and swtpm for privilege
# dropping. The 0.9.x source is distributed as a git tag archive (no
# pre-generated configure), so autoreconf runs first (upstream autogen.sh
# is `touch NEWS` + `autoreconf -fv --install`).

configure() {
    set -e
    touch NEWS
    autoreconf -fv --install
    # --without-python3: the python bindings need swig, which the
    # distribution does not ship, and no InterGenOS consumer uses them —
    # the virtualization consumers (qemu/libvirt/swtpm) link the C library.
    ./configure --prefix=/usr \
                --libdir=/usr/lib \
                --without-python3
}

build() {
    set -e
    make -j"$(nproc)"
}

do_install() {
    set -e
    make DESTDIR="${DESTDIR}" install
}
