#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# freerdp 3.22.0 — Free implementation of the Remote Desktop Protocol
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    # Client selection (operator ruling 2026-07-11, post-burn log sweep; r3):
    # wlfreerdp stays dropped (WITH_WAYLAND=OFF) — upstream flags it
    # "[unmaintained]"; xfreerdp, the maintained X11 client, runs on the Wayland
    # desktop via XWayland. The maintained SDL client is now ENABLED against the
    # in-tree sdl3 package: WITH_CLIENT_SDL3=ON builds the SDL3 client;
    # WITH_CLIENT_SDL2=OFF excludes the upstream-labeled "[deprecated,experimental]"
    # SDL2 client. With only the SDL3 client built the binary is unversioned —
    # /usr/bin/sdl-freerdp (client/SDL/CMakeLists.txt declares project(sdl-freerdp),
    # which renames the sdl3-freerdp target to sdl-freerdp when not versioned).
    cmake -D CMAKE_INSTALL_PREFIX=/usr   \
          -D CMAKE_SKIP_INSTALL_RPATH=ON \
          -D CMAKE_BUILD_TYPE=Release    \
          -D WITH_CAIRO=ON               \
          -D WITH_CLIENT_SDL=ON          \
          -D WITH_CLIENT_SDL3=ON         \
          -D WITH_CLIENT_SDL2=OFF        \
          -D WITH_WAYLAND=OFF            \
          -D WITH_DSP_FFMPEG=ON          \
          -D WITH_FFMPEG=ON              \
          -D WITH_PCSC=OFF               \
          -D WITH_SERVER=ON              \
          -D WITH_SERVER_CHANNELS=ON     \
          -D DOCBOOKXSL_DIR=/usr/share/xml/docbook/xsl-stylesheets-nons-1.79.2 \
          -W no-dev                      \
          -G Ninja ..
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
