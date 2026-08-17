#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# orc 0.4.42 — Oil Runtime Compiler (GStreamer SIMD JIT)
# GStreamer subproject, recommended dep for gst-plugins-base/good/bad and pulseaudio.
# Provides optimized audio/video processing via runtime SIMD code generation.

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=disabled \
          -Dbenchmarks=disabled \
          -Dexamples=disabled \
          -Dhotdoc=disabled
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    cd build
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        ninja test
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
