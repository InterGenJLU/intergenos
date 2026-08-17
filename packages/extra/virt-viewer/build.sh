#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# virt-viewer 11.0 — VM graphical console viewer
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# virt-viewer (libvirt-integrated) + remote-viewer (bare SPICE/VNC
# URIs). SPICE, VNC, and VTE serial-console support all enabled —
# every provider ships in-tree. oVirt support disabled: no oVirt/RHV
# infrastructure consumer exists (it would pull the rest-api stack).

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # Upstream post-11.0 fix, cherry-picked verbatim (commit 98d9f202,
    # "data: remove bogus param for meson i18n.merge_file", 2022-04-26):
    # meson >= 0.61 rejects i18n.merge_file()'s ignored positional
    # argument as a hard error ("Function does not take positional
    # arguments", data/meson.build:4) — the 11.0 tarball carries three
    # such call sites, all covered by this commit (verified: the only
    # merge_file uses in the tree).
    patch -Np1 -i "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0001-data-remove-bogus-param-for-meson-i18n-merge_file.patch"
    meson setup build \
        --prefix=/usr \
        --libdir=/usr/lib \
        --buildtype=release \
        -Dlibvirt=enabled \
        -Dspice=enabled \
        -Dvnc=enabled \
        -Dvte=enabled \
        -Dovirt=disabled
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install
}
