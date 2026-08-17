#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# mingw-w64-headers 14.0.0 — Windows API headers, both PE triplets
# (GE extra-tier wave, RT-15 stage 2). Grounded against the Arch
# mingw-w64-headers PKGBUILD + GLFS; flags per the research doc.
#
# --with-default-msvcrt=msvcrt is EXPLICIT and load-bearing: upstream
# flipped the default CRT to UCRT at v12.0.0 — leaving it unset would
# silently produce a ucrt toolchain. GLFS (the wine-lane precedent) and
# Fedora's classic mingw targets pin msvcrt; wine's PE DLLs are
# msvcrt-linked by design. The crt recipe pins the SAME value — the two
# must always agree.

TRIPLETS="x86_64-w64-mingw32 i686-w64-mingw32"

configure() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        mkdir -p "build-${T}"
        ( cd "build-${T}" &&
          ../mingw-w64-headers/configure                 \
                       --prefix="/usr/${T}"              \
                       --host="${T}"                     \
                       --enable-sdk=all                  \
                       --with-default-msvcrt=msvcrt )
    done
}

build() {
    # Header-only package: nothing to compile (the install target copies).
    :
}

do_install() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        make -C "build-${T}" DESTDIR="$DESTDIR" install
    done
}
