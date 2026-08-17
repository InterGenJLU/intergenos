#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# editorconfig-core-c 0.12.9 — EditorConfig core C library
# Required by: gnome-text-editor

configure() {
    set -e
    mkdir -p build
    cd    build

    cmake ..                              \
          -DCMAKE_INSTALL_PREFIX=/usr     \
          -DCMAKE_BUILD_TYPE=Release
}

build() {
    set -e
    cd build
    make
}

do_install() {
    set -e
    cd build
    make DESTDIR="$DESTDIR" install
}
