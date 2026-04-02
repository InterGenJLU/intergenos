#!/bin/bash
# libwebp 1.5.0 — WebP image format library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                --enable-libwebpmux \
                --enable-libwebpdemux \
                --enable-libwebpdecoder
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
