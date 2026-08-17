#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gnome-keyring 48.0 — GNOME password and secret storage
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -i 's:"/desktop:"/org:' schema/*.xml

    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -D selinux=disabled
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

    # PAM config ships as owned payload (hook-contract wave). Byte/mode-
    # identical to the file the retired hook block wrote (644).
    install -dm755 "${DESTDIR}/etc/pam.d"
    cat > "${DESTDIR}/etc/pam.d/gnome-keyring" << "GKPAM"
auth     optional    pam_gnome_keyring.so
session  optional    pam_gnome_keyring.so auto_start
GKPAM
    chmod 644 "${DESTDIR}/etc/pam.d/gnome-keyring"
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
