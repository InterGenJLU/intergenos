#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# phodav 3.0 — WebDAV server library (chezdav, spice-webdavd)
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# GLib/libsoup3 WebDAV implementation. spice-webdavd provides guest
# folder sharing over the SPICE channel (consumed by the virt-viewer/
# spice-gtk flow); chezdav is the standalone WebDAV server. Avahi
# support is enabled (avahi ships in the desktop tier). The
# spice-webdavd.service unit installs to the systemd unitdir and stays
# disabled by default via the 99-intergenos-default-disable preset
# catch-all (it is a guest-side service the user enables deliberately).
# gtk_doc is disabled: the gtk-doc toolchain is not in the distribution
# and API-reference generation has no consumer in the image.

configure() {
    set -e
    meson setup build \
        --prefix=/usr \
        --libdir=/usr/lib \
        --buildtype=release \
        -Dgtk_doc=disabled \
        -Davahi=enabled \
        -Dsystemdsystemunitdir=/usr/lib/systemd/system \
        -Dudevrulesdir=/usr/lib/udev/rules.d
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install
}
