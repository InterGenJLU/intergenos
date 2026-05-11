#!/bin/bash
# libwebp 1.6.0 — WebP image format library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static \
                --enable-libwebpmux \
                --enable-libwebpdemux \
                --enable-libwebpdecoder \
                --enable-libwebpextras \
                --enable-swap-16bit-csp
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
