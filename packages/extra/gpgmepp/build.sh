#!/bin/bash
# gpgmepp 2.0.0 — C++ wrapper for GPGME
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    cmake -D CMAKE_INSTALL_PREFIX=/usr ..
}

build() {
    cd build
    make -j${IGOS_JOBS}
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" make install
}
