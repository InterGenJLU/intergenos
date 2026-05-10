#!/bin/bash
# miniupnpc 2.3.3 — UPnP IGD client library
# Authored 2026-05-09 to provide the system-library dep that transmission's
# USE_SYSTEM_MINIUPNPC=ON expects. Replaces the bundled-libs symlink hack
# (Halt #34 in Build #6) which only papered over the same root cause that
# eventually halted transmission entirely on libdeflate.

configure() {
    set -e
    mkdir -p build
    cd       build

    cmake -DCMAKE_INSTALL_PREFIX=/usr   \
          -DCMAKE_INSTALL_LIBDIR=lib    \
          -DCMAKE_BUILD_TYPE=Release    \
          -DUPNPC_BUILD_STATIC=ON       \
          -DUPNPC_BUILD_SHARED=ON       \
          -DUPNPC_BUILD_TESTS=OFF       \
          -DUPNPC_BUILD_SAMPLE=OFF      \
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
