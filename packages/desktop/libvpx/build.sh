#!/bin/bash
# libvpx 1.15.0 — VP8/VP9 video codec
# BLFS 13.0

configure() {
    sed -i 's/cp -p/cp/' build/make/Makefile

    mkdir libvpx-build
    cd    libvpx-build

    ../configure --prefix=/usr    \
                 --enable-shared  \
                 --disable-static
}

build() {
    cd libvpx-build
    make -j${IGOS_JOBS}
}

check() {
    cd libvpx-build
    LD_LIBRARY_PATH=. make test || true
}

do_install() {
    cd libvpx-build
    make DESTDIR="$DESTDIR" install
}
