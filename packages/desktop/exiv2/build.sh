#!/bin/bash
# exiv2 0.28.7 — Image metadata library
# BLFS 13.0

configure() {
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
