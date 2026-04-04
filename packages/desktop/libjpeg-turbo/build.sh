#!/bin/bash
# libjpeg-turbo 3.1.3 — High-speed JPEG compression/decompression library
# BLFS 13.0

configure() {
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_INSTALL_DEFAULT_LIBDIR=lib
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
