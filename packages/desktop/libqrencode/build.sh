#!/bin/bash
# libqrencode 4.1.1 — QR code encoding library
# BLFS 13.0

configure() {
    # libqrencode 4.1.1's CMakeLists.txt sets cmake_minimum_required to a
    # version <3.5; CMake 4.x removed compat for that range. The official
    # workaround per CMake's own error message is to set the policy
    # version explicitly. Tracked as candidate for framework-level
    # auto-injection if more packages hit the same compat removal.
    cmake -B build                              \
          -DCMAKE_POLICY_VERSION_MINIMUM=3.5    \
          -DCMAKE_INSTALL_PREFIX=/usr           \
          -DCMAKE_BUILD_TYPE=Release            \
          -DBUILD_TOOLS=OFF
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
