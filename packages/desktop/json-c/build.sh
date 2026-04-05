#!/bin/bash
# json-c 0.18 — JSON library for C
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i 's/VERSION 2.8/VERSION 4.0/' apps/CMakeLists.txt
    sed -i 's/VERSION 3.9/VERSION 4.0/' tests/CMakeLists.txt
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5  \
          -DBUILD_STATIC_LIBS=OFF
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
