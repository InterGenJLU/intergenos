#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# re2 2025-11-05 — regex engine (arrow-cpp compute dep). Links the in-tree
# abseil-cpp (20260107.1 at authoring — re2 requires a 2024+ abseil; the
# find_package is the fail-loud gate on any drift).

configure() {
    set -e
    cmake -B build -G Ninja -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release \
          -DBUILD_SHARED_LIBS=ON -DRE2_BUILD_TESTING=OFF
}

build() {
    set -e
    cmake --build build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
