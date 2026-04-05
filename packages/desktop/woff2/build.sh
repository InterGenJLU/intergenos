#!/bin/bash
# woff2 1.0.2 — Web Open Font Format 2.0 library
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i '/output.h/i #include <cstdint>' src/woff2_out.cc
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5  
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
