#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# lib32-gamemode 1.8.2 — the 32-bit gamemode client libraries (libgamemode +
# libgamemodeauto). Sibling: packages/extra/gamemode (same official meson-dist
# tarball, same version — RT-9 lock). Profile: scripts/lib32-env.sh +
# config/lib32/lib32-cross.ini (RT-7).
#
# Client libs ONLY: -Dwith-sd-bus-provider=no-daemon drops the daemon target
# (gamemode meson gates it on the provider, meson.build:184), so inih — a
# daemon-only dependency — is never pulled; -Dwith-util=false drops gamemoderun
# and the /usr/libexec helpers (all shipped 64-bit by packages/extra/gamemode);
# -Dwith-examples=false drops the samples. The allowlist staging in do_install
# ships ONLY /usr/lib32, so the installed header + pkgconfig-in-/usr the build
# also lays down never leave the private root. --wrap-mode=nodownload keeps the
# build offline (the inih subproject is unreferenced under no-daemon).

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..                                                    \
          --cross-file /mnt/intergenos/config/lib32/lib32-cross.ini   \
          --prefix=/usr                                               \
          --libdir=/usr/lib32                                         \
          --buildtype=release                                         \
          --wrap-mode=nodownload                                      \
          -Dwith-sd-bus-provider=no-daemon                            \
          -Dwith-util=false                                           \
          -Dwith-examples=false
}

build() {
    set -e
    cd build
    # -v MANDATORY: the archive-time time64 log assertion refuses a log with
    # no visible compile evidence (RT-8/F2-a).
    ninja -v
}

do_install() {
    set -e
    cd build
    DESTDIR="$PWD/m32root" ninja install
    lib32_stage_libs "$PWD/m32root"
    lib32_assert_only_lib32
    lib32_env_end
}
