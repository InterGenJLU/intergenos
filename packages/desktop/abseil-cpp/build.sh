#!/bin/bash
# abseil-cpp 20260107.1 — Abseil C++ common libraries
# BLFS 13.0

configure() {
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release \
          -DBUILD_SHARED_LIBS=ON \
          -DABSL_PROPAGATE_CXX_STD=ON
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
