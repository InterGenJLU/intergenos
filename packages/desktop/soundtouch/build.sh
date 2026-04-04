#!/bin/bash
# soundtouch 2.4.0 — Audio tempo/pitch processing library
# BLFS 13.0

configure() {
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
