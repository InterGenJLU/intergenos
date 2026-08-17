#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# lib32-systemd-libs 259.1 — libudev + libsystemd, 32-bit multilib runtime
# (GE arc, operator decision 3 — the grounded "libudev twin").
# Sibling: packages/core/systemd (same tarball, same version — RT-9 lock).
# Profile: scripts/lib32-env.sh + config/lib32/lib32-cross.ini (RT-7: the
# cross file pins every tool the build consults; env vars alone govern
# only pkg-config consumers).
#
# Option-set provenance: every -D name/value verified against the pinned
# 259.1 meson_options.txt (see package.yml). The disable wall is the
# libs-only mechanism's first half (no feature probes 64-bit-only deps);
# the allowlist staging in do_install is the second half (only /usr/lib32
# ships — the built-but-unwanted daemons never leave the private root).

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    mkdir -p build
    cd build

    meson setup ..                                                    \
          --cross-file /mnt/intergenos/config/lib32/lib32-cross.ini   \
          --prefix=/usr                                               \
          --libdir=/usr/lib32                                         \
          --buildtype=release                                         \
          -Dmode=release                                              \
          -Defi=false                                                 \
          -Dbootloader=disabled                                       \
          -Dukify=disabled                                            \
          -Dkernel-install=false                                      \
          -Dpam=disabled                                              \
          -Dseccomp=disabled                                          \
          -Dselinux=disabled                                          \
          -Dapparmor=disabled                                         \
          -Daudit=disabled                                            \
          -Dsmack=false                                               \
          -Dima=false                                                 \
          -Dtpm=false                                                 \
          -Dtpm2=disabled                                             \
          -Dlibcryptsetup=disabled                                    \
          -Dpwquality=disabled                                        \
          -Dopenssl=disabled                                          \
          -Dp11kit=disabled                                           \
          -Dlibfido2=disabled                                         \
          -Dqrencode=disabled                                         \
          -Dblkid=disabled                                            \
          -Dkmod=disabled                                             \
          -Dlz4=disabled                                              \
          -Dxz=disabled                                               \
          -Dzstd=disabled                                             \
          -Dbzip2=disabled                                            \
          -Dlibcurl=disabled                                          \
          -Dlibidn2=disabled                                          \
          -Dmicrohttpd=disabled                                       \
          -Dlibiptc=disabled                                          \
          -Dgnutls=disabled                                           \
          -Dgcrypt=disabled                                           \
          -Dnetworkd=false                                            \
          -Dresolve=false                                             \
          -Dmachined=false                                            \
          -Dportabled=false                                           \
          -Dhomed=disabled                                            \
          -Duserdb=false                                              \
          -Dhibernate=false                                           \
          -Dutmp=false                                                \
          -Dldconfig=false                                            \
          -Dnss-myhostname=false                                      \
          -Dnss-mymachines=disabled                                   \
          -Dnss-resolve=disabled                                      \
          -Dnss-systemd=false                                         \
          -Ddbus=disabled                                             \
          -Dtests=false                                               \
          -Dinstall-tests=false                                       \
          -Dman=disabled                                              \
          -Dhtml=disabled
}

build() {
    set -e
    cd build
    # -v MANDATORY: the archive-time time64 log assertion refuses a log
    # with no visible compile evidence (RT-8/F2-a).
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
