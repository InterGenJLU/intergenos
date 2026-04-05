#!/bin/bash
# libjxl 0.11.2 — JPEG XL image format library
# BLFS 13.0

configure() {
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5  \
          -DBUILD_TESTING=OFF \
          -DBUILD_SHARED_LIBS=ON \
          -DJPEGXL_ENABLE_BENCHMARK=OFF \
          -DJPEGXL_ENABLE_SKCMS=OFF \
          -DJPEGXL_ENABLE_SJPEG=OFF \
          -DJPEGXL_ENABLE_PLUGINS=OFF
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
