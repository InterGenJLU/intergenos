#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# mingw-w64-tools 14.0.0 — <triplet>-widl for both PE triplets
# (GE extra-tier wave). vkd3d-proton's build-win32/64.txt cross files
# name <triplet>-widl as the widl-mingw-tools-fallback; research doc
# alongside this landing.

TRIPLETS="x86_64-w64-mingw32 i686-w64-mingw32"

configure() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        mkdir -p "build-widl-${T}"
        ( cd "build-widl-${T}" &&
          ../mingw-w64-tools/widl/configure              \
                       --prefix=/usr                     \
                       --target="${T}" )
    done
}

build() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        make -C "build-widl-${T}" -j${IGOS_JOBS}
    done
}

do_install() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        make -C "build-widl-${T}" DESTDIR="$DESTDIR" install
    done
}
