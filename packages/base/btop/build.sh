#!/bin/bash
# btop 1.4.4 — Resource monitor
# From upstream (not in BLFS)

configure() {
    cmake -B build \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
