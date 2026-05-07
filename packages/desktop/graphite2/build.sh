#!/bin/bash
# graphite2 1.3.14 — Font rendering engine for complex scripts
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -i '/cmptest/d' tests/CMakeLists.txt
    sed -i 's/PythonInterp/Python3/' CMakeLists.txt
    sed -i '/Font.h/i #include <cstdint>' tests/featuremap/featuremaptest.cpp
    # Fix cmake minimum version for cmake 4.x
    sed -i 's/VERSION 2.8.0/VERSION 3.5/' CMakeLists.txt
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DCMAKE_POLICY_VERSION_MINIMUM=3.5  
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
