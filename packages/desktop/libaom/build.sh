#!/bin/bash
# libaom 3.13.1 — AV1 video codec reference implementation
# BLFS 13.0

configure() {
    # Prevent installing static libraries
    sed -i 's/aom aom_static/aom/' build/cmake/aom_install.cmake

    mkdir aom-build
    cd    aom-build

    cmake -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DBUILD_SHARED_LIBS=1       \
          -DENABLE_DOCS=no            \
          -G Ninja ..
}

build() {
    cd aom-build
    ninja
}

do_install() {
    cd aom-build
    DESTDIR="$DESTDIR" ninja install
}
