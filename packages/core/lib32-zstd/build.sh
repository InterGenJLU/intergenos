#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# Zstd 1.5.7 — 32-bit multilib runtime (GE arc, Wave 1)
# Sibling: packages/core/zstd (same tarball, same version — RT-9 lock).
# Library-only build (make -C lib): this package ships libzstd + its .pc;
# the zstd CLI ships with the 64-bit sibling. Profile: scripts/lib32-env.sh.

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    : # Plain Makefile, no configure
}

build() {
    set -e
    make -C lib prefix=/usr LIBDIR=/usr/lib32 -j${IGOS_JOBS}
}

do_install() {
    set -e
    make -C lib prefix=/usr LIBDIR=/usr/lib32 DESTDIR="$PWD/m32root" install
    rm -fv m32root/usr/lib32/libzstd.a   # mirror the sibling's static-lib drop
    lib32_stage_libs "$PWD/m32root"
    lib32_assert_only_lib32
    lib32_env_end
}
