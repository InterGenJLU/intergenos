#!/bin/bash
# orc 0.4.42 — Oil Runtime Compiler (GStreamer SIMD JIT)
# GStreamer subproject, recommended dep for gst-plugins-base/good/bad and pulseaudio.
# Provides optimized audio/video processing via runtime SIMD code generation.

configure() {
    mkdir build
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
    cd build
    ninja
}

check() {
    cd build
    ninja test || true
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
