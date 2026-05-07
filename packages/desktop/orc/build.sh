#!/bin/bash
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
    ninja test || true
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
