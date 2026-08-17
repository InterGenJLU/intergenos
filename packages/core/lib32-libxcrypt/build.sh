#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# libxcrypt 4.5.2 — 32-bit multilib runtime (GE arc, launch-7 addition)
# Sibling: packages/core/libxcrypt (same tarball, same version — RT-9 lock).
# Flag set mirrors the sibling exactly (strong+glibc hashes, no obsolete
# API — SONAME libcrypt.so.2 — no static, no failure tokens), including the
# sibling's glibc-2.43 const-strchr sed. Profile: scripts/lib32-env.sh.

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    # Fix for glibc-2.43 compatibility (mirrors the 64-bit sibling)
    sed -i '/strchr/s/const//' lib/crypt-{sm3,gost}-yescrypt.c

    ./configure --prefix=/usr                \
        --host=${LIB32_HOST}                 \
        --libdir=/usr/lib32                  \
        --enable-hashes=strong,glibc         \
        --enable-obsolete-api=no             \
        --disable-static                     \
        --disable-failure-tokens
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$PWD/m32root" install
    lib32_stage_libs "$PWD/m32root"
    lib32_assert_only_lib32
    lib32_env_end
}
