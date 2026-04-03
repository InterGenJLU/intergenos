#!/bin/bash
# x265 4.1 — H.265/HEVC video encoder
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -r '/cmake_policy.*(0025|0054)/d' -i source/CMakeLists.txt
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
