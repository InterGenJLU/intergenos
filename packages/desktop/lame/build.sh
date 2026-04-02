#!/bin/bash
# lame 3.100 — MP3 encoder
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --enable-mp3rtp \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
