#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# xdg-user-dirs 0.19 — XDG user directory management
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dsysconfdir=/etc
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

    # GBC002.4 (2026-06-08): ensure xdg-user-dirs-update actually runs at first
    # login under GNOME 49. The upstream autostart /etc/xdg/autostart/
    # xdg-user-dirs.desktop ships X-systemd-skip=true (GNOME 49's systemd
    # xdg-autostart-generator SKIPS it) AND X-GNOME-Autostart-Phase=Initialization
    # (gnome-session 49 no longer honours autostart phases — journal: "gnome-session
    # no longer manages session services"). Net: it ran NOWHERE, so a fresh login
    # got an EMPTY home — no ~/Documents, Downloads, Pictures, Videos, Music,
    # Public, Templates, Desktop (verified on the GBC002 A12 install). Drop both
    # keys so the systemd generator runs it as a plain session autostart.
    _autostart="${DESTDIR}/etc/xdg/autostart/xdg-user-dirs.desktop"
    if [ -f "$_autostart" ]; then
        sed -i -e '/^X-systemd-skip=true$/d' \
               -e '/^X-GNOME-Autostart-Phase=/d' "$_autostart"
    fi
}
