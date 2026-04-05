#!/bin/bash
# exiv2 0.28.7 — Image metadata library
# BLFS 13.0

configure() {
    cmake -B build \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DEXIV2_ENABLE_VIDEO=yes \
        -DEXIV2_ENABLE_WEBREADY=yes \
        -DEXIV2_ENABLE_CURL=yes \
        -DEXIV2_BUILD_SAMPLES=no \
        -G Ninja
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
