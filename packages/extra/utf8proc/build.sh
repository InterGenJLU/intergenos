#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# utf8proc 2.11.3 — UTF-8 processing library (arrow-cpp string-compute dep).

configure() {
    set -e
    cmake -B build -G Ninja -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release \
          -DBUILD_SHARED_LIBS=ON
}

build() {
    set -e
    cmake --build build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
