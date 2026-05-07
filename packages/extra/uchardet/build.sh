#!/bin/bash
# uchardet 0.0.8 — Character encoding detection library
# BLFS 13.0

configure() {
    set -e
    cmake -B build                              \
          -DCMAKE_INSTALL_PREFIX=/usr            \
          -DBUILD_STATIC=OFF                    \
          -DCMAKE_POLICY_VERSION_MINIMUM=3.5    \
          -W no-dev
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
