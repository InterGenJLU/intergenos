#!/bin/bash
# abseil-cpp 20260107.1 — Abseil C++ common libraries
# BLFS 13.0

configure() {
    set -e
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5  \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -DBUILD_SHARED_LIBS=ON \
          -DABSL_PROPAGATE_CXX_STD=ON
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
