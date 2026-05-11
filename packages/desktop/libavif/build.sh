#!/bin/bash
# libavif 1.3.0 — AVIF image format library
# BLFS 13.0

configure() {
    set -e
    # BLFS required fix
    sed 's/enable_adaptive_quantization/aq_mode/' -i src/codec_svt.c

    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5  \
          -DAVIF_CODEC_DAV1D=SYSTEM   \
          -DAVIF_LIBYUV=OFF
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
