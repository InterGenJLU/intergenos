#!/bin/bash
# taglib 2.2 — Library for reading and editing audio file metadata tags
# BLFS 13.0 (https://www.linuxfromscratch.org/blfs/view/13.0/general/taglib.html)
#
# utfcpp is bundled under 3rdparty/utfcpp; CMake auto-detects and uses the
# in-tree copy when no system utfcpp is installed (header-only library).
# Test suite requires Cppunit (not packaged); skipped per BLFS guidance.

configure() {
    set -e
    cmake -B build                       \
          -DCMAKE_INSTALL_PREFIX=/usr    \
          -DCMAKE_BUILD_TYPE=Release     \
          -DBUILD_SHARED_LIBS=ON
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
