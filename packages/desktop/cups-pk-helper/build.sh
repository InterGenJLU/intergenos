#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# cups-pk-helper 0.2.7 — PolicyKit mechanism for CUPS configuration.
#
# Why we ship it: the GNOME Settings -> Printers panel performs printer
# admin actions (add/remove/enable a printer) through this PolicyKit
# mechanism. Without it the panel reports "some settings cannot be
# unlocked" and the user cannot add their printer from the GUI. It pairs
# with the Welcomer "Enable Print Services" toggle (intergen-welcome):
# the toggle starts cups.socket + adds the user to lpadmin, and this
# mechanism lets the Settings panel actually drive cupsd.
#
# meson build (upstream switched to meson; cross-checked against Arch's
# meson build). No package-specific options. Deps glib2 (gio) + cups
# (libcups) + polkit (polkit-gobject-1) all resolve from the system.

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release
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
