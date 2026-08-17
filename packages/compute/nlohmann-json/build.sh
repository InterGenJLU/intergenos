#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# nlohmann-json 3.12.0 — header-only JSON library
#
# JSON_MultipleHeaders=ON per MIOpen's own dependency manifest
# (requirements.txt: nlohmann/json@v3.11.2 -DJSON_MultipleHeaders=ON);
# tests off (header-only install).

configure() {
    set -e
    mkdir -p build
    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DJSON_MultipleHeaders=ON \
        -DJSON_BuildTests=OFF \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
