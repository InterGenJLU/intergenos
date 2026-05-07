#!/bin/bash
# libaom 3.13.1 — AV1 video codec reference implementation
# BLFS 13.0

configure() {
    set -e
    # Prevent installing static libraries
    sed -i 's/aom aom_static/aom/' build/cmake/aom_install.cmake

    mkdir -p aom-build
    cd    aom-build

    cmake -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5  \
          -DBUILD_SHARED_LIBS=1       \
          -DENABLE_DOCS=no            \
          -G Ninja ..
}

build() {
    set -e
    cd aom-build
    ninja
}

do_install() {
    set -e
    cd aom-build
    DESTDIR="$DESTDIR" ninja install
}
