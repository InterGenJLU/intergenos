#!/bin/bash
# woff2 1.0.2 — Web Open Font Format 2.0 library
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i '/output.h/i #include <cstdint>' src/woff2_out.cc
    # Fix cmake_minimum_required for cmake 4.x
    sed -i 's/cmake_minimum_required(VERSION [0-9]\.[0-9.]*)/cmake_minimum_required(VERSION 3.5)/' CMakeLists.txt
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
