#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# Elfutils 0.194 — libelf only, 32-bit multilib runtime (GE arc, launch-7
# addition, GE-01 L18). Sibling: packages/core/elfutils (same tarball,
# same version — RT-9 lock; the sibling is ALREADY libelf-only, LFS 8.50
# shape: make -C lib -C libelf). Consumer that forced it: lib32-mesa's
# radeonsi (meson.build:1917 "requires libelf"). Compression pins per the
# package.yml rationale. Profile: scripts/lib32-env.sh.

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    ./configure --prefix=/usr            \
        --host=${LIB32_HOST}             \
        --libdir=/usr/lib32              \
        --disable-debuginfod             \
        --enable-libdebuginfod=dummy     \
        --with-zstd                      \
        --without-bzlib                  \
        --without-lzma
}

build() {
    set -e
    make -C lib -j${IGOS_JOBS}
    make -C libelf -j${IGOS_JOBS}
}

check() {
    set -e
    : # Sibling parity: the suite fails to build with glibc-2.43+ (see
      # packages/core/elfutils/build.sh) — same skip, same reason.
}

do_install() {
    set -e
    make -C libelf DESTDIR="$PWD/m32root" install
    # configure generated config/libelf.pc with libdir=/usr/lib32; the
    # sibling installs its .pc manually too (make -C libelf skips it).
    install -vDm644 config/libelf.pc \
        "$PWD/m32root/usr/lib32/pkgconfig/libelf.pc"
    rm -f "$PWD/m32root/usr/lib32/libelf.a"
    lib32_stage_libs "$PWD/m32root"
    lib32_assert_only_lib32
    lib32_env_end
}
