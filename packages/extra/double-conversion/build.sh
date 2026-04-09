#!/bin/bash
# double-conversion 3.4.0 — Binary-decimal conversion library
# BLFS 13.0

configure() {
    mkdir -p build
    cd       build

    cmake -DCMAKE_INSTALL_PREFIX=/usr        \
          -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -DBUILD_SHARED_LIBS=ON             \
          -DBUILD_TESTING=OFF                \
          ..
}

build() {
    cd build
    make -j${IGOS_JOBS}
}

do_install() {
    cd build
    make DESTDIR="$DESTDIR" install
}
