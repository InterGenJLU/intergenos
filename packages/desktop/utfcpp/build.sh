#!/bin/bash
# utfcpp 4.0.9 — header-only UTF-8 C++ library
# Used by taglib for unicode handling in audio metadata tags.

configure() {
    set -e
    mkdir -p build
    cd build
    cmake -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release \
          -DUTF8_TESTS=OFF \
          ..
}

build() {
    set -e
    cd build
    cmake --build . -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" cmake --install .
}
