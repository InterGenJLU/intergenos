#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# mingw-w64-binutils 2.46.0 — target binutils for both PE triplets
# (GE extra-tier wave, RT-15 stage 1). Grounded against the Arch
# mingw-w64-binutils PKGBUILD + the GLFS mingw-w64 chapter; flags per the
# research doc landed with this recipe.

TRIPLETS="x86_64-w64-mingw32 i686-w64-mingw32"

configure() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        mkdir -p "build-${T}"
        ( cd "build-${T}" &&
          ../configure --prefix=/usr                          \
                       --target="${T}"                        \
                       --infodir="/usr/share/info/${T}"       \
                       --disable-multilib                     \
                       --disable-nls                          \
                       --enable-deterministic-archives        \
                       --disable-werror )
        # --infodir per target: the info files carry no target prefix, so
        #   without this the two triplet builds collide with each other AND
        #   with the native binutils-core's info tree (Arch's exact fix).
        # --enable-deterministic-archives: reproducible ar members in every
        #   archive this toolchain ever produces (SOURCE_DATE_EPOCH covers
        #   the PE COFF header timestamps at link time — the other half).
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
    # The bfd linker-dependency plugin installs untargeted at
    # /usr/lib/bfd-plugins/libdep.so — both triplet loops write it and the
    # NATIVE binutils already owns that path. Remove it from the cross
    # package (Arch does the same); the native one serves the host.
    rm -f "${DESTDIR}/usr/lib/bfd-plugins/libdep.so"
}
