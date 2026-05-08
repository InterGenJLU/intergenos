#!/bin/bash
# libcbor 0.13.0 — Concise Binary Object Representation (CBOR), RFC 8949
# Required by libfido2 for COSE-encoded FIDO2 authenticator messages.
#
# CMake build. Builds shared library by default.

configure() {
    set -e
    cmake -S . -B build                       \
        -DCMAKE_BUILD_TYPE=Release            \
        -DCMAKE_INSTALL_PREFIX=/usr           \
        -DCMAKE_INSTALL_LIBDIR=lib            \
        -DBUILD_SHARED_LIBS=ON                \
        -DCBOR_CUSTOM_ALLOC=ON                \
        -DWITH_TESTS=OFF                      \
        -Wno-dev
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        cmake --build build --target test
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
