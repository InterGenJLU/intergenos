#!/bin/bash
# svt-av1 4.0.1 — SVT-based AV1 encoder
# BLFS 13.0

configure() {
    cmake -B build                       \
          -DCMAKE_INSTALL_PREFIX=/usr    \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5     \
          -DCMAKE_SKIP_INSTALL_RPATH=ON  \
          -DBUILD_SHARED_LIBS=ON         \
          -Wno-dev                       \
          -G Ninja
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
