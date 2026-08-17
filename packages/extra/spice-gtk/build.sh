#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# spice-gtk 0.42 — GTK SPICE client library
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# Client side of the SPICE remote display protocol: virt-manager and
# virt-viewer render VM consoles through these libraries (via GObject
# introspection, so the typelibs are load-bearing). USB redirection is
# enabled end-to-end (usbredir + the polkit-backed ACL helper +
# libcap-ng privilege drop); webdav folder sharing via phodav.
#
# VERSION PIN (recorded per the latest-stable rule): 0.43 exists as a
# git tag only — upstream published no dist tarball at its canonical
# download location, and the tag archive omits the spice-common
# submodule. Every distribution witness (Gentoo/Alpine/Void/Debian)
# ships 0.42 from the canonical URL below. Revisit at the next
# upstream dist release.
#
# smartcard disabled: needs libcacard, not shipped, no consumer.
# vala/gtk_doc disabled: neither toolchain is in the distribution.

configure() {
    set -e
    meson setup build \
        --prefix=/usr \
        --libdir=/usr/lib \
        --buildtype=release \
        -Dgtk=enabled \
        -Dintrospection=enabled \
        -Dwebdav=enabled \
        -Dusbredir=enabled \
        -Dlibcap-ng=enabled \
        -Dpolkit=enabled \
        -Dlz4=enabled \
        -Dsasl=enabled \
        -Dopus=enabled \
        -Dsmartcard=disabled \
        -Dvapi=disabled \
        -Dgtk_doc=disabled \
        -Dusb-ids-path=/usr/share/hwdata/usb.ids
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install
}
