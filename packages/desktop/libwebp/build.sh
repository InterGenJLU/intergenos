#!/bin/bash
# libwebp 1.6.0 — WebP image format library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                --enable-libwebpmux \
                --enable-libwebpdemux \
                --enable-libwebpdecoder \
                --enable-libwebpextras \
                --enable-swap-16bit-csp
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
