#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libosinfo 1.12.0 — OS and install-media metadata library
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# virt-manager detects guest OSes and picks device defaults through
# this library (via introspection typelibs) against the osinfo-db
# data. PCI/USB IDs come from the shipped hwdata package. gtk-doc and
# vala are disabled (toolchains not in the distribution); the test
# suite build is off (no in-image consumer).

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # Upstream post-1.12.0 fix, cherry-picked verbatim (commit 0adf3853,
    # "loader: don't use libxml2 deprecated APIs", 2025-04-14): the
    # 1.12.0 tarball annotates catchXMLError's msg parameter with
    # libxml2's ATTRIBUTE_UNUSED macro, which libxml2 2.13+ removed
    # from its public headers (we ship 2.15.1) — a hard syntax error
    # at osinfo_loader.c:1905. The commit also migrates the handler
    # off removed direct-struct access (buf->content, ctxt->lastError)
    # to the accessor APIs.
    patch -Np1 -i "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0001-loader-dont-use-libxml2-deprecated-APIs.patch"
    meson setup build \
        --prefix=/usr \
        --libdir=/usr/lib \
        --buildtype=release \
        -Denable-introspection=enabled \
        -Denable-gtk-doc=false \
        -Denable-vala=disabled \
        -Denable-tests=false \
        -Dlibsoup-abi=3.0 \
        -Dwith-pci-ids-path=/usr/share/hwdata/pci.ids \
        -Dwith-usb-ids-path=/usr/share/hwdata/usb.ids
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install
}
