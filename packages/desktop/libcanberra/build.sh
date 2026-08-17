#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libcanberra 0.30 — XDG sound theme and event sounds library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static \
                --disable-oss
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    # Remove .la files before install to prevent libtool relink failures.
    # Libtool relink chokes on GCC 15 when relinking modules during
    # DESTDIR install (file format not recognized on valid .so files).
    find . -name "*.la" -delete

    make DESTDIR="$DESTDIR" install

    # Decided 2026-07-16: upstream installs its autostart triggers into
    # GNOME-2-era directories no modern consumer reads, so the desktop-login
    # chime never fires. The systemd xdg-autostart generator (GNOME 49 user
    # session) reads /etc/xdg/autostart only — relocate the login-sound
    # trigger there. The other two .desktop files are dead surface with no
    # consumer on this system and are dropped: the GDM-2.x LoginWindow
    # ready-sound (GDM 49 reads no such directory) and the
    # gnome-settings-daemon gtk-modules hook (module loading removed from
    # gnome-settings-daemon in 3.32). The mv/rm are fail-loud on purpose —
    # an upstream layout change must halt here, not silently ship.
    install -d "$DESTDIR/etc/xdg/autostart"
    mv "$DESTDIR/usr/share/gnome/autostart/libcanberra-login-sound.desktop" \
       "$DESTDIR/etc/xdg/autostart/libcanberra-login-sound.desktop"
    rmdir "$DESTDIR/usr/share/gnome/autostart"
    rm "$DESTDIR/usr/share/gdm/autostart/LoginWindow/libcanberra-ready-sound.desktop"
    rmdir -p "$DESTDIR/usr/share/gdm/autostart/LoginWindow" 2>/dev/null || true
    rm "$DESTDIR/usr/lib/gnome-settings-daemon-3.0/gtk-modules/canberra-gtk-module.desktop"
    rmdir -p "$DESTDIR/usr/lib/gnome-settings-daemon-3.0/gtk-modules" 2>/dev/null || true
}
