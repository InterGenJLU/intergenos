#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# gamemode 1.8.2 — Feral Interactive game-mode daemon (meson build).
#
# --wrap-mode=nodownload forces the in-tree system inih (which ships inih.pc +
# libinih.so.0) instead of the bundled subprojects/inih.wrap, keeping the build offline
# and auditable; a missing system inih then fails loudly rather than reaching the network.
# -Dwith-sd-bus-provider=systemd -> the daemon speaks sd-bus via libsystemd (our init),
#                                   which is the upstream default and every systemd distro.
# -Dwith-examples=false          -> do not build/ship the sample games.
# with-util (default true) stays ON: it builds gamemoderun + gamemodelist + the privileged
# /usr/libexec helpers (cpugovctl/gpuclockctl/cpucorectl/procsysctl).
#
# Upstream `meson test` is NOT run in the offline chroot (Rule 10, environmental, not a
# mask): the two tests need a live session D-Bus + a running daemon (`gamemoded -v`) and
# a network-less appstreamcli validation of the metainfo — neither is available in the
# build chroot, and starting the daemon there is a hazard. The build-proof is the clean
# compile plus the pre-squashfs verify_paths audit on the four load-bearing artifacts.

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..               \
          --prefix=/usr          \
          --libdir=/usr/lib      \
          --buildtype=release    \
          --wrap-mode=nodownload \
          -Dwith-sd-bus-provider=systemd \
          -Dwith-examples=false
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
