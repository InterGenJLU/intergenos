#!/bin/bash
# suil 0.10.26 — LV2 plugin UI loader (drobilla)
# Provides wrappers so a host toolkit (Gtk on InterGenOS) can embed an
# LV2 plugin GUI written for a different native windowing API (X11 here;
# Cocoa/WIN32 are foreign-platform). InterGenOS is GNOME-first on Wayland,
# so we build the gtk3 + x11 wrappers and skip qt5/qt6/gtk2/cocoa per the
# `use-if-have` dependency policy — Qt6 isn't in the tree, so the qt
# wrappers can't be built. This is policy, not a feature-disable.
# Pkg-config file installed is `suil-0.pc` (versioned per upstream's
# parallel-major-version convention).
# BLFS does not (yet) carry suil — upstream is the source of truth.

configure() {
    set -e
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Ddocs=disabled     \
          -Dhtml=disabled     \
          -Dsinglehtml=disabled \
          -Dtests=disabled    \
          -Dgtk3=enabled      \
          -Dx11=enabled       \
          -Dgtk2=disabled     \
          -Dqt5=disabled      \
          -Dqt6=disabled      \
          -Dcocoa=disabled
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
