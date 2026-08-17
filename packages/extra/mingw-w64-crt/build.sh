#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# mingw-w64-crt 14.0.0 — Windows CRT for both PE triplets
# (GE extra-tier wave, RT-15 stage 4 — first consumer of the bootstrap
# gcc). Grounded against the Arch mingw-w64-crt PKGBUILD + GLFS; flags
# per the research doc.
#
# --with-default-msvcrt=msvcrt matches the headers recipe EXACTLY (the
# two must always agree; upstream default flipped to ucrt at v12.0.0 —
# see the headers recipe's note).

TRIPLETS="x86_64-w64-mingw32 i686-w64-mingw32"

configure() {
    set -e
    local T arch_flags
    for T in ${TRIPLETS}; do
        # Each triplet builds ONLY its own width (Arch's exact per-target
        # split): the x86_64 sysroot carries lib64, the i686 sysroot
        # carries lib32 — no dual-width sysroots, mirroring the
        # two-separate-toolchains decision.
        case "${T}" in
            x86_64-*) arch_flags="--disable-lib32 --enable-lib64" ;;
            i686-*)   arch_flags="--enable-lib32 --disable-lib64" ;;
        esac
        mkdir -p "build-${T}"
        ( cd "build-${T}" &&
          ../mingw-w64-crt/configure                     \
                       --prefix="/usr/${T}"              \
                       --host="${T}"                     \
                       --with-default-msvcrt=msvcrt      \
                       --enable-wildcard                 \
                       ${arch_flags} )
    done
}

build() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        make -C "build-${T}" -j${IGOS_JOBS}
    done
}

do_install() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        make -C "build-${T}" DESTDIR="$DESTDIR" install
    done
    find "${DESTDIR}" -name "*.la" -delete
}
