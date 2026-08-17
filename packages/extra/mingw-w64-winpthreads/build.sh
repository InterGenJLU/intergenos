#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# mingw-w64-winpthreads 14.0.0 — winpthreads for both PE triplets
# (GE extra-tier wave, RT-15 stage 5). Grounded against the Arch
# mingw-w64-winpthreads PKGBUILD + GLFS; flags per the research doc.

TRIPLETS="x86_64-w64-mingw32 i686-w64-mingw32"

configure() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        mkdir -p "build-${T}"
        ( cd "build-${T}" &&
          ../mingw-w64-libraries/winpthreads/configure   \
                       --prefix="/usr/${T}"              \
                       --host="${T}"                     \
                       --enable-static                   \
                       --enable-shared )
        # Both static and shared: gcc-final's posix libstdc++ links the
        # static archive; PE consumers that link dynamically ship
        # libwinpthread-1.dll alongside (DXVK links itself static — its
        # DLLs are self-contained).
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
    # winpthreads installs libwinpthread.la (verbatim lib_LTLIBRARIES in
    # upstream Makefile.am) — delete per Fedora's mingw-family hygiene.
    find "${DESTDIR}" -name "*.la" -delete
}
