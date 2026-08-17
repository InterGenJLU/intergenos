#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# xsimd 14.3.0 — header-only SIMD wrappers (arrow-cpp compute dep).

configure() {
    set -e
    cmake -B build -G Ninja -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release
}

build() {
    set -e
    cmake --build build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
