#!/bin/bash
# libdeflate 1.25 — fast DEFLATE/zlib/gzip codec
# Authored 2026-05-09 to provide the system-library dep that transmission's
# USE_SYSTEM_DEFLATE=ON expects. Replaces the bundled-libs path through
# transmission's tr_add_external_auto_library() which omits BUILD_BYPRODUCTS
# and fails to resolve the static-library output path.

configure() {
    set -e
    mkdir -p build
    cd       build

    cmake -DCMAKE_INSTALL_PREFIX=/usr        \
          -DCMAKE_INSTALL_LIBDIR=lib         \
          -DCMAKE_BUILD_TYPE=Release         \
          -DLIBDEFLATE_BUILD_STATIC_LIB=ON   \
          -DLIBDEFLATE_BUILD_SHARED_LIB=ON   \
          -DLIBDEFLATE_BUILD_GZIP=ON         \
          -DLIBDEFLATE_BUILD_TESTS=OFF       \
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
    make DESTDIR="$DESTDIR" install
}
