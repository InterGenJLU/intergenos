#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# zenity 4.2.2 — GTK dialog utility (GNOME). zenity 4.x is GTK4 + libadwaita
# (the GTK3 line ended at 3.x; cross-checked against Arch's gtk4/libadwaita
# build and Void's template). meson build, mirrors the baobab recipe.
#
# Why we ship it: zenity --question/--password/--list/--entry is the GUI consent
# modal InterGen's permission gate shells out to on every non-web surface
# (D-Bus, CLI, voice). Without it those gated actions cannot be approved at all
# and fall back to a libnotify toast — a weaker consent surface.
#
# Build options (zenity exposes exactly two; verified against meson_options.txt):
#   -Dwebkitgtk=false  — the --html / WebKit form dialogs pull a FULL web engine
#       into a security-sensitive consent path and are used by NONE of the
#       consent dialogs. Upstream defaults this OFF and Arch ships it off; we
#       keep it off to minimize attack surface (security-first) — a full web
#       engine has no place in the permission-consent path. Set it true only if
#       a deliberate need for zenity --html ever arises.
#   -Dmanpage=true     — install man 1 zenity (we build help2man). Default true;
#       set explicitly so `man zenity` is guaranteed present.
#
# No vendored/subproject deps — zenity resolves gtk4/libadwaita from the system.

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release   \
          -Dwebkitgtk=false     \
          -Dmanpage=true
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
