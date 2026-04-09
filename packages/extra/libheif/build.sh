#!/bin/bash
# libheif 1.21.2 — HEIF and AVIF file format decoder and encoder
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    cmake -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DWITH_GDK_PIXBUF=OFF       \
          -DWITH_OpenH264_DECODER=OFF \
          -G Ninja ..
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
