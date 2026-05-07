#!/bin/bash
# spirv-tools 1.4.341.0 — SPIR-V tools
# BLFS 13.0

configure() {
    set -e
    cmake -B build                              \
          -DCMAKE_INSTALL_PREFIX=/usr           \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5            \
          -DSPIRV_WERROR=OFF                    \
          -DBUILD_SHARED_LIBS=ON                \
          -DSPIRV_TOOLS_BUILD_STATIC=OFF        \
          -DSPIRV-Headers_SOURCE_DIR=/usr
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
