#!/bin/bash
# libvpx 1.16.0 — VP8/VP9 video codec
# BLFS 13.0

configure() {
    set -e
    sed -i 's/cp -p/cp/' build/make/Makefile

    mkdir -p libvpx-build
    cd    libvpx-build

    ../configure --prefix=/usr    \
                 --enable-shared  \
                 --disable-static
}

build() {
    set -e
    cd libvpx-build
    make -j${IGOS_JOBS}
}

check() {
    set -e
    cd libvpx-build
    LD_LIBRARY_PATH=. make test || true
}

do_install() {
    set -e
    cd libvpx-build
    make DESTDIR="$DESTDIR" install
}
