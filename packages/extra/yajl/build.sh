#!/bin/bash
# yajl 2.1.0 — Yet Another JSON Library
# Not in BLFS — InterGenOS extra tier

configure() {
    set -e
    # CMake 4.x compatibility:
    # - CMAKE_POLICY_VERSION_MINIMUM=3.5 bypasses cmake_minimum_required <3.5.
    # - CMAKE_POLICY_DEFAULT_CMP0026=OLD allows GET_TARGET_PROPERTY ... LOCATION
    #   (removed pattern in reformatter/ and verify/ CMakeLists).
    cmake -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_INSTALL_LIBDIR=lib \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DCMAKE_POLICY_DEFAULT_CMP0026=OLD
}

build() {
    set -e
    cmake --build build
}

check() {
    set -e
    cd build && ctest --output-on-failure || true
}

do_install() {
    set -e
    cmake --install build --prefix "$DESTDIR/usr"
    install -d "$DESTDIR/usr/share/man/man3"
    install -v -m644 "$BUILD_DIR/libyajl.3" "$DESTDIR/usr/share/man/man3/libyajl.3"
}
BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
