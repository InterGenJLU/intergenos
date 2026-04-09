#!/bin/bash
# uchardet 0.0.8 — Character encoding detection library
# BLFS 13.0

configure() {
    cmake -B build                              \
          -DCMAKE_INSTALL_PREFIX=/usr            \
          -DBUILD_STATIC=OFF                    \
          -DCMAKE_POLICY_VERSION_MINIMUM=3.5    \
          -W no-dev
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
