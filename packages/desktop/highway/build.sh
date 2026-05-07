#!/bin/bash
# highway 1.3.0 — Performance-portable SIMD/vector intrinsics library
# BLFS 13.0

configure() {
    set -e
    mkdir build
    cd    build

    cmake -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5  \
          -DBUILD_TESTING=OFF         \
          -DBUILD_SHARED_LIBS=ON      \
          ..
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" make install
}
