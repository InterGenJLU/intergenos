#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# remmina 1.4.43 — remote desktop client (RDP, VNC, SPICE, SSH).
#
# Two upstream defaults are turned OFF deliberately, and both are network
# behaviour rather than features:
#
#   WITH_NEWS  contacts remmina.org on startup to fetch a news feed.
#   WITH_STATS collects and sends usage statistics.
#
# Neither is asked for by the user, so both are compiled out rather than left
# to a setting that ships enabled. Everything the user chose to connect to is
# unaffected.
#
# The RDP plugin is built against FreeRDP 3 (WITH_FREERDP3), which is the
# version in this tree; upstream still defaults to the FreeRDP 2 symbols.
#
# The icon-cache and desktop-database refreshes are moved out of the install
# target and into post_install, because the upstream targets run those tools
# against the live system during install rather than against the staged root.

configure() {
    set -e
    mkdir -pv build
    cd        build

    cmake -DCMAKE_INSTALL_PREFIX=/usr       \
          -DCMAKE_INSTALL_LIBDIR=lib        \
          -DCMAKE_BUILD_TYPE=Release        \
          -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -DWITH_FREERDP3=ON                \
          -DWITH_NEWS=OFF                   \
          -DWITH_STATS=OFF                  \
          -DWITH_MANPAGES=ON                \
          -DWITH_PYTHONLIBS=ON              \
          -DHAVE_LIBAPPINDICATOR=OFF        \
          -DWITH_WWW=OFF                    \
          -DWITH_GVNC=OFF                   \
          -DWITH_X2GO=OFF                   \
          -DWITH_KF5WALLET=OFF              \
          -DWITH_EXAMPLES=OFF               \
          -DWITH_ICON_CACHE=OFF             \
          -DWITH_UPDATE_DESKTOP_DB=OFF      \
          ..
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd build
    make DESTDIR="$DESTDIR" install
}

post_install() {
    set -e
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true
    update-mime-database /usr/share/mime 2>/dev/null || true
    ldconfig 2>/dev/null || true
}
