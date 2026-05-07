#!/bin/bash
# libqrencode 4.1.1 — QR code encoding library
# BLFS 13.0

configure() {
    set -e
    # libqrencode 4.1.1's CMakeLists.txt sets cmake_minimum_required to a
    # version <3.5; CMake 4.x removed compat for that range. The official
    # workaround per CMake's own error message is to set the policy
    # version explicitly. Tracked as candidate for framework-level
    # auto-injection if more packages hit the same compat removal.
    # CMAKE_POSITION_INDEPENDENT_CODE=ON: build static lib with -fPIC so
    # consumers like gst-plugins-bad's libgstqroverlay.so can link
    # libqrencode.a into shared objects. Without it, ld fails with
    # "relocation R_X86_64_PC32 against symbol QRinput_anTable can not
    # be used when making a shared object".
    cmake -B build                              \
          -DCMAKE_POLICY_VERSION_MINIMUM=3.5    \
          -DCMAKE_INSTALL_PREFIX=/usr           \
          -DCMAKE_BUILD_TYPE=Release            \
          -DCMAKE_POSITION_INDEPENDENT_CODE=ON  \
          -DBUILD_TOOLS=OFF
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
