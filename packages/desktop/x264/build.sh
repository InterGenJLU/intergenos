#!/bin/bash
# x264 20241226 — H.264/AVC video encoder
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --enable-shared \
                --disable-cli
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
