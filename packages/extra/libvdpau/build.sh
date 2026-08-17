#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libvdpau 1.5 — VDPAU dispatcher library
#
# Build profile: stock meson. We disable documentation (would pull in
# doxygen + graphviz which are mirror-only per 2026-05-28 curation walk)
# and force-enable dri2 so the X server-side driver-name query path
# works for legacy apps that don't set VDPAU_DRIVER manually.
#
# Configure flags chosen:
#   --prefix=/usr            standard distro layout
#   --libdir=/usr/lib        avoid lib64/lib pkg-config split
#   --buildtype=release      optimized; no debug shipped
#   -Ddocumentation=false    skip doxygen-built API docs (mirror-only deps)
#   -Ddri2=true              hard-enable the X-server-side driver-name
#                            query (auto would silently disable on a
#                            chroot that's missing dri2proto.pc; we have
#                            xorgproto in tree so this should always
#                            resolve, but explicit is reproducible)
#
# Cross-distro flag comparison:
#   Arch:   -Ddocumentation=false -Ddri2=enabled  (auto in some PKGBUILD revisions)
#   Fedora: -Ddocumentation=false -Ddri2=enabled
#   Debian: -Ddocumentation=false -Ddri2=enabled
# We align exactly.
#
# Security-only-alignment filter: dispatcher library, no daemon, no SUID. Loads
# back-end drivers from /usr/lib/vdpau/ via dlopen(); the back-end
# driver is the trust boundary. Default driver search path is
# /usr/lib/vdpau/ which is root-owned at install time and verified by
# dm-verity at boot.

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..                  \
          --prefix=/usr             \
          --libdir=/usr/lib         \
          --buildtype=release       \
          -Ddocumentation=false     \
          -Ddri2=true
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
