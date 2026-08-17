#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# sdl3 3.4.12 — Simple DirectMedia Layer 3 (cross-platform media/input/GPU).
#
# SDL3 is CMake-only (upstream dropped the SDL2-era autotools path). The
# feature matrix below is pinned EXPLICITLY and each flag is grounded against
# an in-tree dependency, so a missing backend fails the build loudly instead
# of SDL's default auto-detect silently disabling a video/audio driver:
#   SDL_WAYLAND + SDL_WAYLAND_LIBDECOR -> wayland, wayland-protocols, libdecor
#   SDL_X11 (+ Xext/Xcursor/Xrandr/Xi/Xfixes/Xrender/XScrnSaver) -> libX11 stack
#   SDL_VULKAN            -> vulkan-headers (build) / vulkan-loader (dlopened)
#   SDL_OPENGL/OPENGLES/KMSDRM -> mesa, libdrm
#   SDL_ALSA/PULSEAUDIO/PIPEWIRE -> alsa-lib, pulseaudio, pipewire
#   SDL_DBUS -> dbus ; SDL_IBUS -> ibus ; SDL_HIDAPI -> libusb (+ bundled hidapi)
#   SDL_JACK=OFF / SDL_SNDIO=OFF -> no jack/sndio package in the tree; pipewire is
#     our audio routing. Both default ON on UNIX, so the explicit OFF is a real,
#     deterministic pin (not a no-op) — it can't silently enable a backend we
#     don't ship if jack/sndio ever enters the chroot.
#   SDL_HIDAPI_LIBUSB=ON -> explicitly grounds the declared libusb dependency.
# Flag names verified against the pinned SDL3-3.4.12 CMakeLists.txt option set
# (Rule-A.3 distro cross-check vs Arch's sdl3 PKGBUILD: our explicit matrix is
# stricter than upstream/Arch auto-detect; SDL_XINERAMA is not an SDL3 option).
# SDL_TESTS/SDL_EXAMPLES are interactive demo programs that need a live display;
# they are not built or shipped. CMAKE_POLICY_VERSION_MINIMUM=3.5 per build-rules
# §2.3 (custom cmake package, defensive against a pinned pre-3.5 minimum).

configure() {
    set -e
    mkdir -p build
    cd    build

    cmake -D CMAKE_INSTALL_PREFIX=/usr        \
          -D CMAKE_BUILD_TYPE=Release         \
          -D CMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -D CMAKE_SKIP_INSTALL_RPATH=ON      \
          -D SDL_SHARED=ON                    \
          -D SDL_STATIC=OFF                   \
          -D SDL_RPATH=OFF                    \
          -D SDL_TESTS=OFF                    \
          -D SDL_INSTALL_TESTS=OFF            \
          -D SDL_EXAMPLES=OFF                 \
          -D SDL_X11=ON                       \
          -D SDL_WAYLAND=ON                   \
          -D SDL_WAYLAND_LIBDECOR=ON          \
          -D SDL_VULKAN=ON                    \
          -D SDL_OPENGL=ON                    \
          -D SDL_OPENGLES=ON                  \
          -D SDL_KMSDRM=ON                    \
          -D SDL_ALSA=ON                      \
          -D SDL_PULSEAUDIO=ON                \
          -D SDL_PIPEWIRE=ON                  \
          -D SDL_JACK=OFF                     \
          -D SDL_SNDIO=OFF                    \
          -D SDL_DBUS=ON                      \
          -D SDL_IBUS=ON                      \
          -D SDL_HIDAPI=ON                    \
          -D SDL_HIDAPI_LIBUSB=ON             \
          -W no-dev                           \
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
