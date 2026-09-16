#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# modemmanager 1.24.2 — Mobile broadband modem management daemon
# BLFS 13.0

MM_RECIPE_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

install_activation_policy() {
    install -Dm644 "$MM_RECIPE_DIR/files/10-dbus-activation.conf" \
        "$DESTDIR/usr/lib/systemd/system/ModemManager.service.d/10-dbus-activation.conf"
}

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false \
          -Dman=false \
          -Dtests=false \
          -Dbash_completion=false \
          -Dqrtr=false
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
    install_activation_policy
}
