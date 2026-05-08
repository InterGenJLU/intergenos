#!/bin/bash
# yajl 2.1.0 — Yet Another JSON Library
# Not in BLFS — InterGenOS extra tier

configure() {
    set -e
    # CMake 4.x compatibility:
    # - CMAKE_POLICY_VERSION_MINIMUM=3.5 bypasses cmake_minimum_required <3.5.
    # - reformatter/ and verify/ subdirs use GET_TARGET_PROPERTY ... LOCATION,
    #   a pattern entirely removed in CMake 4.x (CMP0026 OLD bypass doesn't
    #   work — the code path is gone). Drop those subdirs from the build
    #   via sed; they're CLI tools (json_reformat, json_verify), not needed
    #   by yajl's lib consumers (crun, podman).
    sed -i 's|^ADD_SUBDIRECTORY(reformatter)|# &|; s|^ADD_SUBDIRECTORY(verify)|# &|' CMakeLists.txt

    cmake -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_INSTALL_LIBDIR=lib \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cmake --build build
}

check() {
    set -e
    cd build
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        ctest --output-on-failure
}

do_install() {
    set -e
    # Don't pass --prefix here: configure already set CMAKE_INSTALL_PREFIX=/usr,
    # and cmake honors DESTDIR env var. Passing both produces a double-nested
    # /tmp/igos-staging/.../tmp/igos-staging/.../usr/ path.
    DESTDIR="$DESTDIR" cmake --install build
    install -d "$DESTDIR/usr/share/man/man3"
    install -v -m644 "$BUILD_DIR/libyajl.3" "$DESTDIR/usr/share/man/man3/libyajl.3"
}
BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
