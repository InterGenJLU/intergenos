#!/bin/bash
# libtiff 4.7.1 — Pass 2 rebuild with libwebp support
# BLFS 13.0
#
# Pass 1 builds libtiff without libwebp because libwebp depends on
# libtiff (circular dependency). After libwebp is installed, this
# pass rebuilds libtiff with WebP format support.

configure() {
    cmake -B build                     \
          -DCMAKE_INSTALL_PREFIX=/usr  \
          -DCMAKE_BUILD_TYPE=Release   \
          -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -DBUILD_SHARED_LIBS=ON
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
