#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# Zlib 1.3.2 — 32-bit multilib runtime (GE arc, Wave 1)
# Sibling: packages/core/zlib (same tarball, same version — RT-9 lock).
# Profile + staging discipline: scripts/lib32-env.sh (G2/T2 — the recipe
# restates NO env values).

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    ./configure --prefix=/usr --libdir=/usr/lib32
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
    rm -fv m32root/usr/lib32/libz.a   # mirror the sibling's static-lib drop
    lib32_stage_libs "$PWD/m32root"
    lib32_assert_only_lib32
    lib32_env_end
}
