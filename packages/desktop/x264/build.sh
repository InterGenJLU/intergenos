#!/bin/bash
# x264 20241226 — H.264/AVC video encoder
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --enable-shared \
                --disable-cli
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
