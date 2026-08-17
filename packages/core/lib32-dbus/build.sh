#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# lib32-dbus 1.16.2 — libdbus-1, the D-Bus client library (32-bit multilib
# runtime). Sibling: packages/core/dbus (same tarball, same version — RT-9 lock).
# Profile: scripts/lib32-env.sh + config/lib32/lib32-cross.ini (RT-7).
#
# Libs-only: -Dmessage_bus=false drops the dbus-daemon (and with it the expat
# edge — meson requires expat ONLY for the bus, meson.build:396-397; the daemon
# stays 64-bit), -Dtools=false drops the client CLIs; the allowlist staging in
# do_install ships ONLY /usr/lib32, so anything else the build produces never
# leaves the private root. -Dsystemd=enabled keeps parity with the 64-bit
# libdbus-1.so.3, which hard-NEEDs libsystemd.so.0 (readelf-verified) — the
# 32-bit libsystemd + its .pc come from lib32-systemd-libs. Every -D name/value
# is verified against the pinned 1.16.2 meson_options.txt.

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
          --wrap-mode=nofallback                                      \
          -Dmessage_bus=false                                         \
          -Dtools=false                                               \
          -Dsystemd=enabled                                           \
          -Dx11_autolaunch=disabled                                   \
          -Dselinux=disabled                                          \
          -Dapparmor=disabled                                         \
          -Dlibaudit=disabled                                         \
          -Dmodular_tests=disabled                                    \
          -Dinstalled_tests=false                                     \
          -Ddoxygen_docs=disabled                                     \
          -Dducktype_docs=disabled                                    \
          -Dxml_docs=disabled
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
